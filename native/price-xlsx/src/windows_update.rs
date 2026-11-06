//! Detached Windows installer transaction. No process discovery or forced app exit.
#[cfg(windows)]
mod implementation {
    use serde::Deserialize;
    use serde_json::{json, Value};
    use sha2::{Digest, Sha256};
    use std::{ffi::c_void, fs, io::Read, path::{Path, PathBuf}, process::{Command, Stdio},
              thread, time::{Duration, Instant}};
    use std::os::windows::{fs::OpenOptionsExt, process::CommandExt};

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Process { pid: u32, path: PathBuf }
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Plan {
        schema: u32, installer: PathBuf, sha256: String, size: u64, version: String,
        target_dir: PathBuf, app_home: PathBuf, processes: Vec<Process>, token: String,
        #[serde(default = "wait_timeout")] wait_timeout_ms: u64,
        #[serde(default)] staging_dir: Option<PathBuf>,
    }
    fn wait_timeout() -> u64 { 60_000 }
    type RawHandle = *mut c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> RawHandle;
        fn QueryFullProcessImageNameW(handle: RawHandle, flags: u32, path: *mut u16, size: *mut u32) -> i32;
        fn WaitForSingleObject(handle: RawHandle, milliseconds: u32) -> u32;
        fn CloseHandle(handle: RawHandle) -> i32;
        fn GetStdHandle(kind: u32) -> RawHandle;
        fn SetHandleInformation(handle: RawHandle, mask: u32, flags: u32) -> i32;
    }
    #[link(name = "advapi32")]
    unsafe extern "system" {
        fn RegOpenKeyExW(key: RawHandle, subkey: *const u16, options: u32, access: u32, result: *mut RawHandle) -> i32;
        fn RegCloseKey(key: RawHandle) -> i32;
        fn RegQueryValueExW(key: RawHandle, name: *const u16, reserved: *mut u32, kind: *mut u32, data: *mut u8, size: *mut u32) -> i32;
        fn RegSetValueExW(key: RawHandle, name: *const u16, reserved: u32, kind: u32, data: *const u8, size: u32) -> i32;
    }
    struct Handle(RawHandle);
    impl Drop for Handle { fn drop(&mut self) { unsafe { CloseHandle(self.0); } } }
    fn normalized(path: &Path) -> Result<String, String> {
        let path = path.canonicalize().map_err(|e| format!("更新路径无效：{e}"))?;
        Ok(path.to_string_lossy().trim_start_matches(r"\\?\").to_lowercase())
    }
    impl Handle {
        fn open(process: &Process) -> Result<Self, String> {
            let raw = unsafe { OpenProcess(0x100000 | 0x1000, 0, process.pid) };
            if raw.is_null() { return Err(format!("无法确认待退出进程 {}：{}", process.pid, std::io::Error::last_os_error())); }
            let handle = Self(raw);
            let mut path = vec![0u16; 32768];
            let mut size = path.len() as u32;
            if unsafe { QueryFullProcessImageNameW(raw, 0, path.as_mut_ptr(), &mut size) } == 0 {
                return Err("无法核对待退出进程的程序路径".into());
            }
            let actual = PathBuf::from(String::from_utf16_lossy(&path[..size as usize]));
            if normalized(&actual)? != normalized(&process.path)? { return Err("待退出进程的程序路径不匹配，已阻止更新".into()); }
            Ok(handle)
        }
        fn exited(&self) -> Result<bool, String> {
            match unsafe { WaitForSingleObject(self.0, 0) } {
                0 => Ok(true), 258 => Ok(false), _ => Err("无法确认旧程序是否退出".into()),
            }
        }
    }
    fn atomic_json(path: &Path, value: &Value) -> Result<(), String> {
        fs::create_dir_all(path.parent().ok_or("更新状态目录无效")?).map_err(|e| e.to_string())?;
        let temporary = path.with_extension("tmp");
        fs::write(&temporary, serde_json::to_vec(value).unwrap()).map_err(|e| e.to_string())?;
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match fs::rename(&temporary, path) {
                Ok(()) => return Ok(()),
                Err(error) if Instant::now() >= deadline => return Err(error.to_string()),
                Err(_) => thread::sleep(Duration::from_millis(20)),
            }
        }
    }
    fn report(cache: &Path, plan: &Plan, mut value: Value) -> Result<(), String> {
        value["cache"] = json!(cache);
        value["mode"] = json!(if plan.schema == 2 {"delta"} else {"full"});
        atomic_json(&cache.join("status.json"), &value)?;
        atomic_json(&plan.app_home.join("state/windows-update.json"), &value)
    }
    fn decision(cache: &Path, plan: &Plan) -> Option<String> {
        let value: Value = serde_json::from_slice(&fs::read(cache.join("decision.json")).ok()?).ok()?;
        if value["token"] != plan.token { return None; }
        value["action"].as_str().map(str::to_owned)
    }
    fn validate(plan: &Plan, cache: &Path) -> Result<Vec<Handle>, String> {
        let valid_version = plan.version.split('.').count() >= 2 && plan.version.split('.').count() <= 4
            && plan.version.split('.').all(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()));
        if !matches!(plan.schema,1|2) || (plan.schema == 2) != plan.staging_dir.is_some()
            || !valid_version || plan.token.len() < 8 || plan.token.len() > 80
            || !plan.token.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || plan.sha256.len() != 64 || !plan.sha256.bytes().all(|b| b.is_ascii_hexdigit())
            || plan.size == 0 || plan.size > 2 * 1024u64.pow(3)
            || !(100..=60_000).contains(&plan.wait_timeout_ms)
            || !plan.target_dir.is_absolute() || !plan.app_home.is_absolute()
            || plan.processes.len() != 3 {
            return Err("Windows 更新计划无效".into());
        }
        let target = plan.target_dir.canonicalize().map_err(|e| e.to_string())?;
        let home = plan.app_home.canonicalize().map_err(|e| e.to_string())?;
        let installer = plan.installer.canonicalize().map_err(|e| e.to_string())?;
        if let Some(stage) = &plan.staging_dir {
            crate::windows_delta::plain(&plan.target_dir)?;
            let stage = crate::windows_delta::stage_path(&target, stage, &plan.token)?;
            if home.starts_with(&stage) || cache.starts_with(&stage) || plan.size > 32*1024*1024 {return Err("Windows 增量目录或清单大小无效".into());}
        }
        if target.parent().is_none() || target.file_name().is_none() || home.starts_with(&target)
            || installer.parent() != Some(cache) || cache.starts_with(&target)
            || !target.join("payload/manifest.json").is_file()
            || normalized(&plan.processes[0].path)? != normalized(&target.join("electricity-toolbox.exe"))? {
            return Err("安装目录、数据目录或安装包位置无效".into());
        }
        let mut pids = std::collections::HashSet::new();
        let mut handles = Vec::new();
        for process in &plan.processes {
            if process.pid == std::process::id() || !pids.insert(process.pid) { return Err("待退出进程标识重复或无效".into()); }
            handles.push(Handle::open(process)?);
        }
        Ok(handles)
    }
    fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
        let mut command = Command::new(program);
        command.creation_flags(0x08000000).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        command
    }
    fn registry_snapshot(cache: &Path) -> Result<Option<PathBuf>, String> {
        let reg = PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into())).join("System32/reg.exe");
        let key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\电力工具箱";
        if !registry_key_exists(key)? { return Ok(None); }
        let snapshot = cache.join("uninstall-registry.reg");
        if !command(&reg).args(["export", key]).arg(&snapshot).arg("/y").status().map_err(|e| e.to_string())?.success() {
            return Err("无法备份卸载信息，已阻止更新".into());
        }
        Ok(Some(snapshot))
    }
    fn registry_open_status(status: i32) -> Result<bool, String> {
        match status { 0 => Ok(true), 2 => Ok(false), _ => Err(format!("无法读取卸载信息（Windows 错误码 {status}），已阻止更新")) }
    }
    fn registry_key_exists(key: &str) -> Result<bool, String> {
        let subkey: Vec<u16> = key.strip_prefix("HKCU\\").ok_or("卸载信息键无效")?.encode_utf16().chain(Some(0)).collect();
        let mut handle = std::ptr::null_mut();
        let status = unsafe { RegOpenKeyExW((-2147483647isize) as RawHandle, subkey.as_ptr(), 0, 0x20019, &mut handle) };
        if status == 0 { unsafe { RegCloseKey(handle); } }
        registry_open_status(status)
    }
    fn restore_registry(snapshot: &Option<PathBuf>) -> Result<(), String> {
        restore_registry_key(snapshot, r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\电力工具箱")
    }
    fn restore_registry_key(snapshot: &Option<PathBuf>, key: &str) -> Result<(), String> {
        let reg = PathBuf::from(std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into())).join("System32/reg.exe");
        if registry_key_exists(key)?
            && !command(&reg).args(["delete", key, "/f"]).status().map_err(|e| e.to_string())?.success() {
            return Err("旧程序已恢复，但无法还原卸载信息".into());
        }
        if let Some(path) = snapshot {
            if !command(reg).arg("import").arg(path).status().map_err(|e| e.to_string())?.success() {
                return Err("旧程序已恢复，但卸载信息恢复失败".into());
            }
        }
        Ok(())
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn delta_updates_only_matching_quoted_install_location() {
            let reg=PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/reg.exe");
            let subkey=format!(r"Software\ElectricityToolboxDeltaTest{}",std::process::id());
            let key=format!(r"HKCU\{subkey}");
            struct Cleanup(PathBuf,String);impl Drop for Cleanup{fn drop(&mut self){let _=command(&self.0).args(["delete",&self.1,"/f"]).status();}}
            let _cleanup=Cleanup(reg.clone(),key.clone());
            let target=std::env::temp_dir();
            let quoted=format!("\"{}\"",target.display());
            assert!(command(&reg).args(["add",&key,"/v","InstallLocation","/d",&quoted,"/f"]).status().unwrap().success());
            let plan:Plan=serde_json::from_value(json!({"schema":2,"installer":"unused","sha256":"unused","size":1,"version":"9.1.2",
                "target_dir":target,"app_home":target,"processes":[],"token":"test-token","staging_dir":target})).unwrap();
            update_registered_version_key(&plan,&subkey).unwrap();
            let result=command(&reg).args(["query",&key,"/v","DisplayVersion"]).stdout(Stdio::piped()).output().unwrap();
            assert!(String::from_utf8_lossy(&result.stdout).contains("9.1.2"));
            assert!(command(&reg).args(["add",&key,"/v","InstallLocation","/d",r"C:\does-not-exist-toolbox-foreign","/f"]).status().unwrap().success());
            let mut plan=plan;plan.version="9.1.3".into();update_registered_version_key(&plan,&subkey).unwrap();
            let result=command(&reg).args(["query",&key,"/v","DisplayVersion"]).stdout(Stdio::piped()).output().unwrap();
            assert!(String::from_utf8_lossy(&result.stdout).contains("9.1.2"));
        }
        #[test]
        fn rollback_restores_exact_registry_key_including_absence() {
            assert!(registry_open_status(5).is_err(),"access denied must not mean missing metadata");
            assert!(!registry_open_status(2).unwrap());
            let reg = PathBuf::from(std::env::var_os("SystemRoot").unwrap()).join("System32/reg.exe");
            let key = format!(r"HKCU\Software\ElectricityToolboxUpdateTest{}", std::process::id());
            struct Cleanup(PathBuf, String, PathBuf);
            impl Drop for Cleanup { fn drop(&mut self) { let _ = command(&self.0).args(["delete",&self.1,"/f"]).status();let _ = fs::remove_file(&self.2); } }
            let snapshot = std::env::temp_dir().join(format!("toolbox-registry-{}.reg",std::process::id()));
            let _cleanup = Cleanup(reg.clone(),key.clone(),snapshot.clone());
            assert!(command(&reg).args(["add",&key,"/v","Original","/d","before","/f"]).status().unwrap().success());
            assert!(command(&reg).args(["export",&key]).arg(&snapshot).arg("/y").status().unwrap().success());
            assert!(command(&reg).args(["add",&key,"/v","Added","/d","new","/f"]).status().unwrap().success());
            restore_registry_key(&Some(snapshot),&key).unwrap();
            assert!(!command(&reg).args(["query",&key,"/v","Added"]).status().unwrap().success(),"rollback left an installer-created value");
            assert!(command(&reg).args(["query",&key,"/v","Original"]).status().unwrap().success());
            restore_registry_key(&None,&key).unwrap();
            assert!(!command(&reg).args(["query",&key]).status().unwrap().success(),"rollback created an originally absent key");
        }
    }
    fn install(plan: &Plan, cache: &Path) -> Result<(u32, Option<String>), String> {
        // Keep the verified file locked against writes/deletion until NSIS exits.
        let mut installer = fs::OpenOptions::new().read(true).share_mode(1).open(&plan.installer).map_err(|e| e.to_string())?;
        let mut digest = Sha256::new();
        let mut buffer = [0u8; 256 * 1024];
        if installer.metadata().map_err(|e| e.to_string())?.len() != plan.size { return Err("安装包大小校验失败".into()); }
        loop {
            let count = installer.read(&mut buffer).map_err(|e| e.to_string())?;
            if count == 0 { break; }
            digest.update(&buffer[..count]);
        }
        if format!("{:x}", digest.finalize()) != plan.sha256.to_lowercase() { return Err("安装包 SHA-256 校验失败".into()); }
        if let Some(stage) = &plan.staging_dir {
            crate::windows_delta::stage_path(&plan.target_dir,stage,&plan.token)?;
            crate::windows_delta::verify_prepared(&plan.installer,&plan.target_dir,stage,&plan.version)?;
        }
        let backup = plan.target_dir.with_file_name(format!(".electricity-toolbox-rollback-{}", plan.token));
        let failed = plan.target_dir.with_file_name(format!(".electricity-toolbox-failed-{}", plan.token));
        if backup.exists() || failed.exists() { return Err("更新恢复目录已存在".into()); }
        let snapshot = registry_snapshot(cache)?;
        fs::rename(&plan.target_dir, &backup).map_err(|e| format!("旧程序目录暂不能替换，未开始安装：{e}"))?;
        let outcome = (|| -> Result<u32, String> {
            let target_text = plan.target_dir.to_str().ok_or("安装路径编码无效")?;
            if target_text.contains(['"', '\r', '\n']) { return Err("安装路径包含无效字符".into()); }
            // NSIS requires /D= to be last and unquoted, including when it has spaces.
            if let Some(stage) = &plan.staging_dir {
                fs::rename(stage,&plan.target_dir).map_err(|e|format!("增量目录切换失败：{e}"))?;
                update_registered_version(plan)?;
            } else {
                crate::windows_delta::preserve_extras(&backup,&plan.target_dir)?;
                let status = command(&plan.installer).args(["/S", "/UPDATE"])
                    .raw_arg(format!("/D={target_text}")).status().map_err(|e| e.to_string())?;
                if !status.success() { return Err(format!("Windows 安装程序退出码 {}", status.code().unwrap_or(-1))); }
            }
            let manifest: Value = serde_json::from_slice(&fs::read(plan.target_dir.join("payload/manifest.json")).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
            if manifest["version"] != plan.version || !plan.target_dir.join("electricity-toolbox.exe").is_file() {
                return Err("安装后的版本或程序文件校验失败".into());
            }
            relaunch(plan)
        })();
        let pid = match outcome { Ok(pid) => pid, Err(error) => {
            if plan.target_dir.exists() { fs::rename(&plan.target_dir, &failed).map_err(|e| format!("{error}；旧版保存在 {}，恢复目录失败：{e}", backup.display()))?; }
            fs::rename(&backup, &plan.target_dir).map_err(|e| format!("{error}；旧版保存在 {}，自动恢复失败：{e}", backup.display()))?;
            restore_registry(&snapshot)?;
            if failed.exists() { let _ = fs::remove_dir_all(&failed); }
            return Err(format!("{error}；已恢复旧程序，配置和数据保留"));
        }};
        // The validated sibling backup contains only the old program, never app_home.
        Ok((pid, fs::remove_dir_all(&backup).err().map(|e| format!("旧版备份尚未清理：{e}"))))
    }
    fn relaunch(plan: &Plan) -> Result<u32, String> {
        command(plan.target_dir.join("electricity-toolbox.exe"))
            .current_dir(&plan.target_dir).env("TOOLBOX_APP_HOME", &plan.app_home)
            .env_remove("TOOLBOX_WORKSPACE").spawn().map(|child| child.id()).map_err(|e| format!("自动启动失败，请重新打开工具箱：{e}"))
    }
    fn update_registered_version(plan:&Plan)->Result<(),String> {
        update_registered_version_key(plan,r"Software\Microsoft\Windows\CurrentVersion\Uninstall\电力工具箱")
    }
    fn update_registered_version_key(plan:&Plan,key:&str)->Result<(),String> {
        let wide=|s:&str|s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let key=wide(key);
        let mut handle=std::ptr::null_mut();
        let status=unsafe{RegOpenKeyExW((-2147483647isize) as RawHandle,key.as_ptr(),0,0x20019|2,&mut handle)};
        if !registry_open_status(status)?{return Ok(());}
        struct Key(RawHandle);impl Drop for Key{fn drop(&mut self){unsafe{RegCloseKey(self.0);}}}
        let _key=Key(handle);let mut buffer=vec![0u16;32768];let mut size=(buffer.len()*2) as u32;let mut kind=0;
        let status=unsafe{RegQueryValueExW(handle,wide("InstallLocation").as_ptr(),std::ptr::null_mut(),&mut kind,buffer.as_mut_ptr() as *mut u8,&mut size)};
        if status==2{return Ok(());}
        if status!=0 || kind!=1 || size%2!=0 || size<2{return Err("卸载信息安装目录无效".into());}
        let text=String::from_utf16_lossy(&buffer[..size as usize/2]).trim_end_matches('\0').to_owned();
        // NSIS stores InstallLocation wrapped in quotes. A stale/foreign
        // installation key must not prevent a portable sibling update.
        let location=PathBuf::from(text.trim_matches('"'));
        if normalized(&location).ok()!=Some(normalized(&plan.target_dir)?){return Ok(());}
        let version=wide(&plan.version);
        let status=unsafe{RegSetValueExW(handle,wide("DisplayVersion").as_ptr(),0,1,version.as_ptr() as *const u8,(version.len()*2) as u32)};
        if status!=0{return Err(format!("无法更新 Windows 版本信息（{status}）"));}Ok(())
    }
    pub fn run(path: &Path) -> Result<(), String> {
        // Python redirects this detached helper's stderr to helper.log. Windows
        // can inherit that handle even when Command replaces the child's stdio.
        // Keep the helper's streams usable, but prevent installer/relaunch leaks.
        for kind in [-10i32, -11, -12] {
            let handle = unsafe { GetStdHandle(kind as u32) };
            if !handle.is_null() && handle as isize != -1
                && unsafe { SetHandleInformation(handle, 1, 0) } == 0 {
                return Err(format!("无法隔离更新助手的日志句柄：{}", std::io::Error::last_os_error()));
            }
        }
        let cache = path.parent().ok_or("更新计划路径无效")?.canonicalize().map_err(|e| e.to_string())?;
        let plan: Plan = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        let handles = validate(&plan, &cache)?;
        report(&cache, &plan, json!({"state":"ready","version":plan.version}))?;
        let mut exited = false;
        let outcome = (|| -> Result<(u32, Option<String>), String> {
            let deadline = Instant::now() + Duration::from_secs(30);
            loop {
                match decision(&cache, &plan).as_deref() {
                    Some("commit") => break,
                    Some("abort") => return Err("已取消更新".into()),
                    _ if Instant::now() >= deadline => return Err("更新提交超时，未替换程序".into()),
                    _ => thread::sleep(Duration::from_millis(20)),
                }
            }
            let deadline = Instant::now() + Duration::from_millis(plan.wait_timeout_ms);
            loop {
                if decision(&cache, &plan).as_deref() == Some("abort") { return Err("已取消更新".into()); }
                if handles.iter().map(Handle::exited).collect::<Result<Vec<_>,_>>()?.iter().all(|done| *done) { exited = true; break; }
                if Instant::now() >= deadline { return Err("旧程序尚未完全退出，未替换程序".into()); }
                thread::sleep(Duration::from_millis(20));
            }
            report(&cache, &plan, json!({"state":"installing","version":plan.version}))?;
            install(&plan, &cache)
        })();
        match outcome {
            Ok((pid, warning)) => {
                report(&cache, &plan, json!({"state":"succeeded","version":plan.version,"relaunch_pid":pid,"cleanup_warning":warning}))
            }
            Err(error) => {
                let aborted = error == "已取消更新";
                if let Some(stage)=&plan.staging_dir {
                    if stage.exists() && crate::windows_delta::stage_path(&plan.target_dir,stage,&plan.token).is_ok(){let _=crate::windows_delta::cleanup(stage);}
                }
                report(&cache, &plan, json!({"state":if aborted {"aborted"} else {"failed"},"version":plan.version,"error":error}))?;
                if exited && !aborted && plan.target_dir.join("electricity-toolbox.exe").is_file() { let _ = relaunch(&plan); }
                if aborted { Ok(()) } else { Err(error) }
            }
        }
    }
}

pub fn run(path: &std::path::Path) -> Result<(), String> {
    #[cfg(windows)] { implementation::run(path) }
    #[cfg(not(windows))] { let _ = path; Err("Windows 安装助手仅适用于 Windows".into()) }
}
