//! Independent validation of prepared Windows updates before directory replacement.
#![cfg(windows)]
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, HashSet}, fs, io::Read, os::windows::fs::MetadataExt, path::{Path, PathBuf}};

#[derive(Deserialize, PartialEq, Debug)]
#[serde(tag="type", rename_all="lowercase", deny_unknown_fields)]
enum Entry { Dir, File { size: u64, sha256: String } }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Prepared { format: u32, platform: String, base: String, target: String,
    before: BTreeMap<String,Entry>, after: BTreeMap<String,Entry> }
#[link(name="normaliz")]
unsafe extern "system" { fn NormalizeString(form:i32, source:*const u16, length:i32, output:*mut u16, capacity:i32)->i32; }
fn collision_key(name:&str)->Result<String,String> {
    let source:Vec<u16>=name.encode_utf16().collect();
    let length=unsafe{NormalizeString(1,source.as_ptr(),source.len() as i32,std::ptr::null_mut(),0)};
    if length<=0{return Err("Windows 文件名规范化失败".into());}
    let mut output=vec![0u16;length as usize];
    let length=unsafe{NormalizeString(1,source.as_ptr(),source.len() as i32,output.as_mut_ptr(),length)};
    if length<=0{return Err("Windows 文件名规范化失败".into());}
    Ok(String::from_utf16(&output[..length as usize]).map_err(|e|e.to_string())?.to_uppercase())
}
fn valid_name(name:&str)->bool {
    !name.is_empty() && name.encode_utf16().count()<=32760 && name.split('/').all(|part|{
        let device=part.split('.').next().unwrap_or("").trim_end_matches(' ').to_uppercase();
        let reserved=matches!(device.as_str(),"CON"|"PRN"|"AUX"|"NUL") ||
            ((device.starts_with("COM") || device.starts_with("LPT")) && device.chars().count()==4 && matches!(device.chars().nth(3),Some('1'..='9'|'¹'|'²'|'³')));
        !part.is_empty() && part!="." && part!=".." && !part.ends_with([' ','.']) && !reserved &&
            !part.chars().any(|c|c<' ' || "\\<>:\"|?*".contains(c))
    })
}
pub fn plain(path:&Path)->Result<fs::Metadata,String> {
    let info=fs::symlink_metadata(path).map_err(|e|e.to_string())?;
    if info.file_attributes()&0x400!=0 || !(info.is_file() || info.is_dir()) {return Err("Windows 更新目录包含链接或特殊文件".into());}
    Ok(info)
}
fn managed(name:&str)->bool {matches!(name,"electricity-toolbox.exe"|"WebView2Loader.dll"|"uninstall.exe"|"payload") || name.starts_with("payload/")}
fn validate(entries:&BTreeMap<String,Entry>)->Result<(),String> {
    if entries.is_empty() || entries.len()>100_000{return Err("Windows 文件清单大小无效".into());}
    let mut keys=HashSet::new();let mut total=0u64;
    for (name,item) in entries {
        if !valid_name(name) || !managed(name) || !keys.insert(collision_key(name)?) {return Err("Windows 文件清单路径无效或重复".into());}
        if let Entry::File{size,sha256}=item {
            if *size>2*1024u64.pow(3) || sha256.len()!=64 || !sha256.bytes().all(|c|c.is_ascii_digit() || (b'a'..=b'f').contains(&c)){return Err("Windows 文件摘要无效".into());}
            total=total.checked_add(*size).ok_or("Windows 文件大小溢出")?;
        }
        let mut parent=Path::new(name).parent();
        while let Some(path)=parent {
            if path.as_os_str().is_empty(){break;}
            let text=path.to_string_lossy().replace('\\',"/");
            if !matches!(entries.get(&text),Some(Entry::Dir)){return Err("Windows 父目录清单无效".into());}
            parent=path.parent();
        }
    }
    if total>12*1024u64.pow(3) || !matches!(entries.get("payload"),Some(Entry::Dir)) ||
        !matches!(entries.get("payload/manifest.json"),Some(Entry::File{..})) ||
        !matches!(entries.get("electricity-toolbox.exe"),Some(Entry::File{..})) {return Err("Windows 文件清单缺少程序或体积超限".into());}
    Ok(())
}
fn hash(path:&Path)->Result<String,String> {
    let mut file=fs::File::open(path).map_err(|e|e.to_string())?;
    let mut digest=Sha256::new();let mut buffer=[0u8;256*1024];
    loop{let n=file.read(&mut buffer).map_err(|e|e.to_string())?;if n==0{break;}digest.update(&buffer[..n]);}
    Ok(format!("{:x}",digest.finalize()))
}
fn walk(root:&Path,path:&Path,actual:&mut BTreeMap<String,Entry>)->Result<(),String> {
    let info=plain(path)?;
    let name=path.strip_prefix(root).map_err(|e|e.to_string())?.to_string_lossy().replace('\\',"/");
    if !valid_name(&name) || actual.len()>=100_000 {return Err("Windows 程序目录无效".into());}
    if info.is_file() && info.len()>2*1024u64.pow(3){return Err("Windows 程序文件过大".into());}
    let item=if info.is_dir(){Entry::Dir}else{Entry::File{size:info.len(),sha256:hash(path)?}};
    actual.insert(name,item);
    if info.is_dir(){for child in fs::read_dir(path).map_err(|e|e.to_string())?{walk(root,&child.map_err(|e|e.to_string())?.path(),actual)?;}}
    Ok(())
}
fn verify(root:&Path,expected:&BTreeMap<String,Entry>)->Result<(),String> {
    fn inspect(path:&Path,count:&mut usize)->Result<(),String>{
        *count+=1;if *count>100_000{return Err("Windows 程序目录文件过多".into());}
        if plain(path)?.is_dir(){for child in fs::read_dir(path).map_err(|e|e.to_string())?{
            let child=child.map_err(|e|e.to_string())?;
            if !valid_name(&child.file_name().to_string_lossy()){return Err("Windows 程序目录包含不安全文件名".into());}
            inspect(&child.path(),count)?;
        }}Ok(())
    }
    inspect(root,&mut 0)?;validate(expected)?;let mut actual=BTreeMap::new();
    for name in ["electricity-toolbox.exe","WebView2Loader.dll","uninstall.exe","payload"] {
        let path=root.join(name);if path.try_exists().map_err(|e|e.to_string())?{walk(root,&path,&mut actual)?;}
    }
    if &actual!=expected{return Err("Windows 增量程序文件校验失败，请改用完整安装包".into());}
    Ok(())
}
fn version(root:&Path)->Result<String,String> {
    let value:serde_json::Value=serde_json::from_slice(&fs::read(root.join("payload/manifest.json")).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    value["version"].as_str().map(str::to_owned).ok_or("Windows 程序版本无效".into())
}
pub fn stage_path(root:&Path,stage:&Path,token:&str)->Result<PathBuf,String> {
    if !stage.is_absolute() || stage.file_name().and_then(|s|s.to_str())!=Some(&format!(".electricity-toolbox-stage-{token}")) ||
        stage.parent().ok_or("Windows 暂存目录无效")?.canonicalize().map_err(|e|e.to_string())?!=root.parent().ok_or("Windows 安装目录无效")?.canonicalize().map_err(|e|e.to_string())? {
        return Err("Windows 增量暂存目录不属于本次更新".into());
    }
    plain(stage)?;stage.canonicalize().map_err(|e|e.to_string())
}
pub fn verify_prepared(metadata:&Path,root:&Path,stage:&Path,target:&str)->Result<(),String> {
    if plain(metadata)?.len()>32*1024*1024{return Err("Windows 增量清单过大".into());}
    let prepared:Prepared=serde_json::from_slice(&fs::read(metadata).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    if prepared.format!=1 || prepared.platform!="windows-x64" || prepared.target!=target || prepared.base!=version(root)? || prepared.target!=version(stage)? {
        return Err("Windows 增量版本或平台不匹配".into());
    }
    verify(root,&prepared.before)?;verify(stage,&prepared.after)?;
    if extras(root)?!=extras(stage)?{return Err("本地附加文件在准备更新后发生变化，已保留旧程序，请重试".into());}Ok(())
}
fn extras(root:&Path)->Result<BTreeMap<String,Entry>,String> {
    let mut entries=BTreeMap::new();
    for child in fs::read_dir(root).map_err(|e|e.to_string())?{
        let child=child.map_err(|e|e.to_string())?;
        if !managed(&child.file_name().to_string_lossy().to_lowercase()){walk(root,&child.path(),&mut entries)?;}
    }Ok(entries)
}
pub fn preserve_extras(old:&Path,new:&Path)->Result<(),String> {
    let entries=extras(old)?;
    fs::create_dir(new).map_err(|e|e.to_string())?;
    // The newly generated uninstaller belongs to the new NSIS package.
    let mut rows:Vec<_>=entries.iter().collect();rows.sort_by_key(|(name,_)|(name.matches('/').count(),*name));
    for (name,item) in rows {
        if name.eq_ignore_ascii_case("uninstall.exe"){continue;}
        let target=new.join(name);
        match item {
            Entry::Dir=>fs::create_dir(&target).map_err(|e|e.to_string())?,
            Entry::File{..}=>{fs::copy(old.join(name),target).map_err(|e|e.to_string())?;}
        }
    }Ok(())
}
pub fn cleanup(stage:&Path)->Result<(),String> {
    // First inspect every entry; never recursively follow a junction introduced later.
    fn check(path:&Path)->Result<(),String>{if plain(path)?.is_dir(){for child in fs::read_dir(path).map_err(|e|e.to_string())?{check(&child.map_err(|e|e.to_string())?.path())?;}}Ok(())}
    if stage.try_exists().map_err(|e|e.to_string())?{check(stage)?;fs::remove_dir_all(stage).map_err(|e|e.to_string())?;}Ok(())
}
