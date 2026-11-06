//! Native ownership of fingerprints, directory locks and recoverable replacement.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[cfg(windows)]
fn identity(file: &File) -> Result<Vec<Value>> {
    use std::os::windows::io::AsRawHandle;
    #[repr(C)]
    struct Basic {
        creation: i64,
        access: i64,
        write: i64,
        change: i64,
        attributes: u32,
    }
    #[repr(C)]
    struct Id {
        volume: u64,
        id: [u8; 16],
    }
    unsafe extern "system" {
        fn GetFileInformationByHandleEx(
            h: *mut std::ffi::c_void,
            class: i32,
            data: *mut std::ffi::c_void,
            size: u32,
        ) -> i32;
    }
    let mut basic = Basic {
        creation: 0,
        access: 0,
        write: 0,
        change: 0,
        attributes: 0,
    };
    let mut id = Id {
        volume: 0,
        id: [0; 16],
    };
    unsafe {
        if GetFileInformationByHandleEx(
            file.as_raw_handle(),
            0,
            &mut basic as *mut _ as *mut _,
            std::mem::size_of::<Basic>() as u32,
        ) == 0
            || GetFileInformationByHandleEx(
                file.as_raw_handle(),
                18,
                &mut id as *mut _ as *mut _,
                std::mem::size_of::<Id>() as u32,
            ) == 0
        {
            return Err(std::io::Error::last_os_error().into());
        }
    }
    let nanos = |time: i64| (time - 116444736000000000) * 100;
    Ok(vec![
        json!(id.volume),
        json!(u64::from_le_bytes(id.id[..8].try_into()?)),
        json!(file.metadata()?.len()),
        json!(nanos(basic.write)),
        json!(nanos(basic.change)),
    ])
}
#[cfg(unix)]
fn identity(file: &File) -> Result<Vec<Value>> {
    use std::os::unix::fs::MetadataExt;
    let m = file.metadata()?;
    Ok(vec![
        json!(m.dev()),
        json!(m.ino()),
        json!(m.len()),
        json!(m.mtime() * 1_000_000_000 + m.mtime_nsec()),
        json!(m.ctime() * 1_000_000_000 + m.ctime_nsec()),
    ])
}
pub fn fingerprint(path: &Path) -> Result<Value> {
    let mut source = File::open(path)?;
    let mut before = identity(&source)?;
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let n = source.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
    }
    // Compare handles on Windows: path stat and fstat use different timestamps.
    let named = File::open(path)?;
    if before != identity(&source)? || before != identity(&named)? {
        return Err(format!("读取期间工作簿被修改，停止写入：{}", path.display()).into());
    }
    before.push(json!(format!("{:x}", digest.finalize())));
    Ok(json!(before))
}
fn unchanged(path: &Path, expected: &Value) -> Result<()> {
    let actual = fingerprint(path).map_err(|e| {
        if !path.exists() {
            format!("原工作簿已被移走，停止写入：{}", path.display())
        } else {
            e.to_string()
        }
    })?;
    if &actual != expected {
        return Err(format!("准备或提交期间工作簿被修改，停止写入：{}", path.display()).into());
    }
    Ok(())
}

pub struct DirectoryLock {
    #[cfg(windows)]
    handle: *mut std::ffi::c_void,
    #[cfg(unix)]
    _file: File,
}
#[cfg(windows)]
unsafe extern "system" {
    fn CreateMutexW(
        attr: *mut std::ffi::c_void,
        owner: i32,
        name: *const u16,
    ) -> *mut std::ffi::c_void;
    fn WaitForSingleObject(handle: *mut std::ffi::c_void, ms: u32) -> u32;
    fn ReleaseMutex(handle: *mut std::ffi::c_void) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}
impl DirectoryLock {
    fn acquire(path: &Path) -> Result<Self> {
        let busy = || format!("该目录有其他工作簿正在提交，请稍后重试：{}", path.display());
        #[cfg(windows)]
        {
            let resolved = path
                .canonicalize()?
                .to_string_lossy()
                .trim_start_matches(r"\\?\")
                .to_lowercase();
            let name = format!(
                "Global\\ElectricityToolbox.Output.{:x}",
                Sha256::digest(resolved.as_bytes())
            );
            let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
            let handle = unsafe { CreateMutexW(std::ptr::null_mut(), 0, wide.as_ptr()) };
            if handle.is_null() {
                return Err(std::io::Error::last_os_error().into());
            }
            let status = unsafe { WaitForSingleObject(handle, 0) };
            if status != 0 && status != 128 {
                unsafe { CloseHandle(handle) };
                return Err(busy().into());
            }
            Ok(Self { handle })
        }
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            unsafe extern "C" {
                fn flock(fd: i32, operation: i32) -> i32;
            }
            let file = File::open(path)?;
            if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
                return Err(busy().into());
            }
            Ok(Self { _file: file })
        }
    }
}
impl Drop for DirectoryLock {
    fn drop(&mut self) {
        #[cfg(windows)]
        unsafe {
            ReleaseMutex(self.handle);
            CloseHandle(self.handle);
        }
    }
}

fn copy_preserved(source: &Path, target: &Path) -> Result<()> {
    fs::copy(source, target)?;
    let metadata = fs::metadata(source)?;
    let times = std::fs::FileTimes::new()
        .set_accessed(metadata.accessed()?)
        .set_modified(metadata.modified()?);
    #[cfg(windows)]
    let copy = {
        use std::os::windows::fs::OpenOptionsExt;
        // FILE_WRITE_ATTRIBUTES permits timestamps without requesting data
        // writes, so copied read-only workbook permissions stay untouched.
        File::options().read(true).access_mode(0x100).open(target)?
    };
    #[cfg(unix)]
    let copy = File::open(target)?; // futimens/futimes does not require O_WRONLY.
    copy.set_times(times)?;
    Ok(())
}
fn unique() -> String {
    format!(
        "{:x}{:x}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    )
}
struct Item {
    original: PathBuf,
    temporary: PathBuf,
    expected: Value,
    rollback: Option<PathBuf>,
    backup: Option<PathBuf>,
    installed: Option<Value>,
}
pub struct Transaction {
    items: Vec<Item>,
    _locks: Vec<DirectoryLock>,
    finished: bool,
}
impl Transaction {
    fn begin(payload: &Value) -> Result<Self> {
        let mut transaction = Self {
            items: Vec::new(),
            _locks: Vec::new(),
            finished: false,
        };
        let mut parents = BTreeSet::new();
        let mut originals = BTreeSet::new();
        for item in payload["items"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[])
        {
            let original = PathBuf::from(item["original"].as_str().ok_or("missing original")?);
            let resolved = original.canonicalize()?;
            if !originals.insert(resolved.clone()) {
                return Err("同一工作簿不能重复提交。".into());
            }
            parents.insert(resolved.parent().ok_or("missing directory")?.to_path_buf());
            transaction.items.push(Item {
                original,
                temporary: PathBuf::from(item["temporary"].as_str().ok_or("missing temporary")?),
                expected: item["fingerprint"].clone(),
                rollback: None,
                backup: None,
                installed: None,
            });
        }
        for path in payload["paths"]
            .as_array()
            .map(Vec::as_slice)
            .unwrap_or(&[])
        {
            let p = Path::new(path.as_str().ok_or("invalid lock path")?);
            // Lock the output directory even when the destination has not yet
            // been created. Prepared originals above still require identity.
            parents.insert(p.parent().ok_or("missing directory")?.canonicalize()?);
        }
        for parent in parents {
            transaction._locks.push(DirectoryLock::acquire(&parent)?);
        }
        for item in &transaction.items {
            unchanged(&item.original, &item.expected)?;
        }
        for item in &mut transaction.items {
            let stem = item.original.file_stem().unwrap().to_string_lossy();
            let rollback =
                item.original
                    .with_file_name(format!(".{}.{}.rollback.xlsx", stem, unique()));
            item.rollback = Some(rollback.clone());
            copy_preserved(&item.original, &rollback)?;
            if payload["create_backup"] == true {
                let suffix = item
                    .original
                    .extension()
                    .map(|x| format!(".{}", x.to_string_lossy()))
                    .unwrap_or_default();
                let stamp = payload["timestamp"].as_str().unwrap_or("native");
                let mut backup = item
                    .original
                    .with_file_name(format!("{}.备份-{}{}", stem, stamp, suffix));
                if backup.exists() {
                    backup = item.original.with_file_name(format!(
                        "{}.备份-{}-{}{}",
                        stem,
                        stamp,
                        unique(),
                        suffix
                    ));
                }
                copy_preserved(&item.original, &backup)?;
                item.backup = Some(backup);
            }
        }
        Ok(transaction)
    }
    fn install(&mut self, index: usize) -> Result<Value> {
        let item = self
            .items
            .get_mut(index)
            .ok_or("invalid transaction index")?;
        if item.installed.is_some() {
            return Err("重复替换工作簿".into());
        }
        let expected = fingerprint(&item.temporary)?;
        unchanged(&item.original, &item.expected)?;
        fs::rename(&item.temporary, &item.original)?;
        item.installed = Some(expected); // register before caller's verifier runs
        Ok(json!({"installed":index}))
    }
    fn rollback(&mut self) -> Vec<String> {
        let mut notes = Vec::new();
        for item in self.items.iter_mut().rev() {
            let Some(rollback) = item.rollback.take() else {
                continue;
            };
            if let Some(expected) = item.installed.take() {
                let restore = (|| -> Result<()> {
                    let actual = fingerprint(&item.original)?;
                    if actual.as_array().unwrap()[..4] != expected.as_array().unwrap()[..4]
                        || actual[5] != expected[5]
                    {
                        return Err("提交后工作簿已被外部修改，保留当前文件".into());
                    }
                    fs::rename(&rollback, &item.original)?;
                    Ok(())
                })();
                if let Err(error) = restore {
                    notes.push(format!(
                        "回滚失败，原文件副本保留于 {}：{}",
                        rollback.display(),
                        error
                    ));
                    continue;
                }
            }
            let _ = fs::remove_file(&rollback);
        }
        self.finished = true;
        notes
    }
    fn finalize(&mut self) -> Result<Value> {
        if self.items.iter().any(|i| i.installed.is_none()) {
            return Err("尚有工作簿未替换".into());
        }
        let backups: Vec<Value> = self
            .items
            .iter()
            .map(|i| json!({"original":i.original,"backup":i.backup}))
            .collect();
        // All installed files have passed the caller's business verification.
        // Commit before deleting recovery state: cleanup cannot safely trigger
        // rollback once any prior recovery copy has been removed.
        self.finished = true;
        let mut cleanup_warnings = Vec::new();
        for item in &mut self.items {
            if let Some(p) = &item.rollback {
                match fs::remove_file(p) {
                    Ok(()) => item.rollback = None,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                        item.rollback = None
                    }
                    Err(error) => cleanup_warnings.push(format!(
                        "工作簿已提交；恢复副本清理失败，副本保留于 {}：{}",
                        p.display(),
                        error
                    )),
                }
            }
        }
        Ok(json!({"backups":backups,"cleanup_warnings":cleanup_warnings}))
    }
}
impl Drop for Transaction {
    fn drop(&mut self) {
        if !self.finished {
            for note in self.rollback() {
                eprintln!("{}", note);
            }
        }
        for item in &self.items {
            let _ = fs::remove_file(&item.temporary);
        }
    }
}

pub fn transaction_stdio() -> Result<()> {
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    let mut transaction: Option<Transaction> = None;
    for line in stdin.lock().lines() {
        let request: Value = serde_json::from_str(&line?)?;
        let action = request["action"].as_str().unwrap_or("");
        let result: Result<Value> = match action {
            "begin" if transaction.is_none() => Transaction::begin(&request).map(|t| {
                transaction = Some(t);
                json!({"begun":true})
            }),
            "install" => transaction
                .as_mut()
                .ok_or_else(|| "transaction not begun".into())
                .and_then(|t| t.install(request["index"].as_u64().unwrap_or(u64::MAX) as usize)),
            "finalize" => transaction
                .as_mut()
                .ok_or_else(|| "transaction not begun".into())
                .and_then(Transaction::finalize),
            "rollback" => Ok(
                json!({"notes":transaction.as_mut().map(Transaction::rollback).unwrap_or_default()}),
            ),
            _ => Err("invalid transaction action".into()),
        };
        let response = match result {
            Ok(result) => json!({"result":result}),
            Err(error) => json!({"error":error.to_string()}),
        };
        writeln!(out, "{}", response)?;
        out.flush()?;
        if action == "finalize" || action == "rollback" {
            break;
        }
    }
    // EOF or protocol failure drops RAII transaction, restoring every owned file.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn readonly_copy_preserves_permissions_and_timestamps() {
        let root = std::env::temp_dir().join(format!("toolbox-readonly-copy-{}", unique()));
        fs::create_dir(&root).unwrap();
        let source = root.join("source.xlsx");
        let target = root.join("copy.xlsx");
        fs::write(&source, b"original").unwrap();
        let mut permissions = fs::metadata(&source).unwrap().permissions();
        permissions.set_readonly(true);
        fs::set_permissions(&source, permissions).unwrap();
        let result = copy_preserved(&source, &target);
        let snapshot = if result.is_ok() {
            Some((
                fs::read(&target).unwrap(),
                fs::metadata(&target).unwrap().permissions().readonly(),
                fs::metadata(&source).unwrap().modified().unwrap(),
                fs::metadata(&target).unwrap().modified().unwrap(),
            ))
        } else {
            None
        };
        for path in [&source, &target] {
            if path.exists() {
                let mut p = fs::metadata(path).unwrap().permissions();
                p.set_readonly(false);
                fs::set_permissions(path, p).unwrap();
            }
        }
        fs::remove_dir_all(root).unwrap();
        result.unwrap();
        let (bytes, readonly, before, after) = snapshot.unwrap();
        assert_eq!(bytes, b"original");
        assert!(readonly);
        assert_eq!(before, after);
    }
}
