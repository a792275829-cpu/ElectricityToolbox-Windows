//! Headless task protocol 3. Python is a short-lived engine adapter, not a scheduler.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]
mod service_process;
mod windows_update;
mod windows_delta;
use regex::Regex;
use serde_json::{json, Value};
use std::{collections::{HashMap, VecDeque}, fs, io::{self, BufRead, BufReader, Write},
    path::PathBuf, process::{Command, Stdio}, sync::{Arc, Mutex, OnceLock,
    atomic::{AtomicBool, AtomicU64, Ordering}, mpsc}, thread, time::{Duration, Instant}};

#[derive(Clone)]
struct Config { workspace: PathBuf, code_root: PathBuf, python: PathBuf }
struct Task { value: Value, cancelled: Arc<AtomicBool> }
#[derive(Default)]
struct State {
    tasks: HashMap<String, Task>, order: Vec<String>, requests: HashMap<String, (String, String)>,
    journal: VecDeque<Value>, sequence: u64, maintenance: bool,
}
type Shared = Arc<Mutex<State>>;
static NEXT: AtomicU64 = AtomicU64::new(1);

fn redact(text: &str) -> String {
    static PATTERNS: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| vec![
        (Regex::new(r"(?im)(authorization|cookie|set-cookie)\s*[:=][^\n]+").unwrap(), "$1: [已隐藏]"),
        (Regex::new(r#"(?i)(["']?(?:password|passwd|username|access_token|refresh_token|token|secret|密码|账号)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}\n]+)"#).unwrap(), "$1[已隐藏]"),
        (Regex::new(r#"(?i)(--(?:password|username|token|secret)\s+)(?:"[^"]*"|'[^']*'|\S+)"#).unwrap(), "$1[已隐藏]"),
        (Regex::new(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@").unwrap(), "$1[已隐藏]@"),
    ]);
    let mut result = text.to_string();
    for (pattern, replacement) in patterns { result = pattern.replace_all(&result, *replacement).into_owned(); }
    static URL: OnceLock<Regex> = OnceLock::new();
    URL.get_or_init(|| Regex::new(r#"https?://[^\s<>"']+"#).unwrap()).replace_all(&result, |capture: &regex::Captures| {
        capture[0].split_once('?').map(|(base, _)| format!("{base}?[参数已隐藏]")).unwrap_or_else(|| capture[0].to_string())
    }).into_owned()
}

fn active(task: &Value) -> bool { matches!(task["state"].as_str(), Some("created" | "running" | "cancelling")) }
fn event(state: &mut State, id: &str, kind: &str, message: &str) {
    state.sequence += 1;
    let message: String = redact(message).chars().take(16000).collect();
    state.journal.push_back(json!({"sequence":state.sequence,"task_id":id,"kind":kind,"message":message,"progress":null}));
    while state.journal.len() > 2000 { state.journal.pop_front(); }
}
fn transition(shared: &Shared, id: &str, mut phase: &str, mut result: Value, mut error: Option<String>) {
    let mut state = shared.lock().unwrap();
    if phase == "running" && state.tasks.get(id).is_some_and(|task| task.cancelled.load(Ordering::Acquire)) { return; }
    if matches!(phase, "succeeded" | "failed") && state.tasks.get(id).is_some_and(|task| task.cancelled.load(Ordering::Acquire)) {
        phase = "cancelled"; result = Value::Null; error = None;
    }
    if let Some(task) = state.tasks.get_mut(id) {
        task.value["state"] = json!(phase);
        task.value["result"] = result.clone();
        task.value["error"] = json!(error.as_ref().map(|e| redact(e)));
        task.value["message"] = json!(error.as_ref().map(|e| redact(e)).unwrap_or_else(||
            if phase == "succeeded" { redact(result["message"].as_str().unwrap_or("已完成")) }
            else if phase == "cancelled" { "任务已取消".to_string() } else { String::new() }));
    }
    if let Some(error) = error { event(&mut state, id, "error", &error); }
    event(&mut state, id, "state", phase);
}

fn compatibility(config: &Config, method: &str, params: &Value) -> Result<Value, String> {
    let mut command = Command::new(&config.python);
    service_process::hide_console(&mut command);
    let mut child = command.args(["-u", "-B"]).arg(config.code_root.join("desktop/python/service_compat.py"))
        .arg("--workspace").arg(&config.workspace).current_dir(&config.workspace)
        .env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().map_err(|e| format!("无法启动兼容引擎：{e}"))?;
    {
        let mut input = child.stdin.take().unwrap();
        serde_json::to_writer(&mut input, &json!({"method":method,"params":params})).map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if !output.status.success() { return Err(format!("兼容引擎退出码 {}：{}", output.status.code().unwrap_or(-1), redact(&String::from_utf8_lossy(&output.stderr)))); }
    let reply: Value = serde_json::from_slice(&output.stdout).map_err(|_| "兼容引擎未返回有效 JSON".to_string())?;
    if let Some(error) = reply["error"].as_str() { return Err(error.to_string()); }
    reply.get("result").cloned().ok_or_else(|| "兼容引擎响应缺少结果".to_string())
}

fn run_worker(config: Config, shared: Shared, id: String, prepared: Value, request: Value, cancelled: Arc<AtomicBool>) {
    if cancelled.load(Ordering::Acquire) { transition(&shared, &id, "cancelled", Value::Null, None); return; }
    transition(&shared, &id, "running", Value::Null, None);
    let request_path = std::env::temp_dir().join(format!("toolbox-native-task-{}-{id}.json", std::process::id()));
    let mut request_created = false;
    let mut owned_child = None;
    let outcome = (|| -> Result<Value, String> {
        let mut options = fs::OpenOptions::new(); options.write(true).create_new(true);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
        let stream = options.open(&request_path).map_err(|e| e.to_string())?;
        request_created = true;
        serde_json::to_writer(stream, &json!({"tool_id":request["tool_id"],"action":request.get("action").cloned().unwrap_or(json!("run")),"values":prepared["values"]})).map_err(|e| e.to_string())?;
        if cancelled.load(Ordering::Acquire) { return Err("任务已取消".into()); }
        let mut command = Command::new(prepared["python"].as_str().unwrap_or(config.python.to_str().unwrap_or("python")));
        command.args(["-u", "-B"]).arg(prepared["worker"].as_str().ok_or("缺少业务引擎入口")?)
            .arg("--workspace").arg(&config.workspace).arg("--request").arg(&request_path)
            .current_dir(&config.workspace).env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")
            .env("TOOLBOX_PYTHON", &config.python).env("PYTHONDONTWRITEBYTECODE", "1");
        owned_child = Some(service_process::OwnedChild::spawn_if_active(&mut command, &cancelled,
            request["tool_id"] == "wps-writer").map_err(|e| e.to_string())?);
        let owned = owned_child.as_mut().unwrap();
        let (sender, receiver) = mpsc::sync_channel::<Option<String>>(128);
        let stdout = owned.child.stdout.take().unwrap();
        let stderr = owned.child.stderr.take().unwrap();
        fn reader(input: impl io::Read + Send + 'static, sender: mpsc::SyncSender<Option<String>>) {
            thread::spawn(move || {
                let mut reader = BufReader::new(input);
                loop {
                    let mut bytes = Vec::new();
                    match reader.read_until(b'\n', &mut bytes) {
                        Ok(0) | Err(_) => break,
                        Ok(_) => { if sender.send(Some(String::from_utf8_lossy(&bytes).replace("\r\n", "\n"))).is_err() { return; } }
                    }
                }
                let _ = sender.send(None);
            });
        }
        reader(stdout, sender.clone()); reader(stderr, sender);
        let mut ended = 0;
        let mut status = None;
        let mut result = None;
        let mut trace = String::new();
        let mut no_data = None;
        let mut termination_error = None;
        while ended < 2 {
            if status.is_none() {
                status = owned.child.try_wait().map_err(|e| e.to_string())?;
            }
            if cancelled.load(Ordering::Acquire) || status.is_some() {
                if let Err(error) = owned.terminate() {
                    let message = format!("后台进程终止尚未确认：{error}");
                    if termination_error.as_ref() != Some(&message) {
                        event(&mut shared.lock().unwrap(), &id, "error", &message);
                        termination_error = Some(message);
                    }
                }
            }
            match receiver.recv_timeout(Duration::from_millis(20)) {
                Ok(None) => ended += 1,
                Ok(Some(line)) => {
                    if let Some(value) = line.strip_prefix("TOOLBOX_RESULT=") {
                        result = Some(serde_json::from_str::<Value>(value).map_err(|_| "业务完成结果无效".to_string())?);
                    } else {
                        let safe = redact(&line);
                        if (!trace.is_empty() || line.contains("Traceback (most recent call last)")) && trace.len() < 16000 {
                            trace.extend(safe.chars().take(16000usize.saturating_sub(trace.chars().count())));
                        }
                        if line.starts_with("无数据：") { no_data = Some(line.trim().to_string()); }
                        event(&mut shared.lock().unwrap(), &id, "log", &safe);
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {},
            }
        }
        let status = status.unwrap_or(owned.child.wait().map_err(|e| e.to_string())?);
        if cancelled.load(Ordering::Acquire) { return Err("任务已取消".into()); }
        if !status.success() { return Err(format!("子进程执行失败，退出码 {}{}", status.code().unwrap_or(-1), if trace.is_empty() { String::new() } else { format!("\n{trace}") })); }
        let result = result.filter(|v| !v.is_null()).ok_or("业务进程未返回完成结果，请核对日志")?;
        Ok(no_data.map(|message| json!({"message":message,"outputs":[]})).unwrap_or(result))
    })();
    if let Some(owned) = owned_child.as_mut() {
        service_process::retry_cleanup(|| owned.cleanup(Duration::from_secs(2)), |error| {
            // Keep running/cancelling so replacement, updates and shutdown stay
            // blocked until every owned process handle confirms completion.
            let mut state = shared.lock().unwrap();
            if let Some(task) = state.tasks.get_mut(&id) {
                task.value["message"] = json!("后台进程清理尚未确认，保留所有权并重试");
            }
            event(&mut state, &id, "error", &format!("后台进程清理尚未确认：{error}"));
        });
    }
    if request_created { let _ = fs::remove_file(request_path); }
    if cancelled.load(Ordering::Acquire) { transition(&shared, &id, "cancelled", Value::Null, None); }
    else { match outcome { Ok(result) => transition(&shared, &id, "succeeded", result, None), Err(error) => transition(&shared, &id, "failed", Value::Null, Some(error)) } }
}

fn start(config: &Config, shared: &Shared, params: &Value, closed: &AtomicBool) -> Result<Value, String> {
    let request_id = params["request_id"].as_str().filter(|s| !s.is_empty() && s.chars().count() <= 100).ok_or("任务缺少有效请求标识")?;
    let tool = params["tool_id"].as_str().ok_or("工具标识无效")?;
    let fingerprint = serde_json::to_string(&json!([params["tool_id"],params.get("action").cloned().unwrap_or(json!("run")),params.get("values").cloned().unwrap_or(json!({}))])).unwrap();
    {
        let state = shared.lock().unwrap();
        if closed.load(Ordering::Acquire) { return Err("工具箱正在退出".into()); }
        if state.maintenance { return Err("工具箱正在更新，请等待更新完成".into()); }
        if let Some((before, id)) = state.requests.get(request_id) {
            return if before == &fingerprint { Ok(json!(id)) } else { Err("同一请求标识不能用于不同参数".into()) };
        }
        if state.tasks.values().any(|t| active(&t.value) && t.value["tool_id"] == tool) { return Err("该工具已有任务运行，请等待完成".into()); }
    }
    let prepared = compatibility(config, "__prepare_start", params)?;
    if !prepared["confirmation"].as_str().unwrap_or("").is_empty() && params["confirmed"] != true { return Err("请先确认上传、覆盖或文件归类操作".into()); }
    let id = format!("{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed));
    let cancelled = Arc::new(AtomicBool::new(false));
    {
        let mut state = shared.lock().unwrap();
        state.tasks.insert(id.clone(), Task { value: json!({"id":id,"tool_id":tool,"name":prepared["name"],"state":"created","cancellable":prepared["cancellable"],"message":"","result":null,"error":null}), cancelled: cancelled.clone() });
        state.order.push(id.clone());
        state.requests.insert(request_id.to_string(), (fingerprint, id.clone()));
        event(&mut state, &id, "state", "created");
    }
    let (config, shared, worker_id, request) = (config.clone(), shared.clone(), id.clone(), params.clone());
    thread::spawn(move || run_worker(config, shared, worker_id, prepared, request, cancelled));
    Ok(json!(id))
}

fn call(config: &Config, shared: &Shared, closed: &AtomicBool, method: &str, params: &Value) -> Result<Value, String> {
    if !params.is_object() { return Err("请求参数必须是对象".into()); }
    match method {
        "ping" => Ok(json!({"protocol":3,"ui":"headless","engine":"rust","workspace":config.workspace,"pid":std::process::id()})),
        "start" => start(config, shared, params, closed),
        "tasks" => { let state = shared.lock().unwrap(); Ok(Value::Array(state.order.iter().map(|id| state.tasks[id].value.clone()).collect())) },
        "events" => {
            let cursor = match params.get("after") {
                None => 0,
                Some(cursor) => cursor.as_u64().ok_or("事件游标无效")?,
            };
            let state = shared.lock().unwrap();
            let oldest = state.journal.front().and_then(|v| v["sequence"].as_u64()).unwrap_or(state.sequence + 1);
            Ok(json!({"cursor":state.sequence,"truncated":cursor < oldest - 1,"pending":0,"events":state.journal.iter().filter(|v| v["sequence"].as_u64().unwrap() > cursor).cloned().collect::<Vec<_>>()}))
        },
        "cancel" => {
            let mut state = shared.lock().unwrap();
            let id = params["task_id"].as_str().unwrap_or("");
            let accepted = if let Some(task) = state.tasks.get_mut(id) {
                if active(&task.value) && task.value["cancellable"] == true {
                    task.cancelled.store(true, Ordering::Release); task.value["state"] = json!("cancelling"); true
                } else { false }
            } else { false };
            if accepted { event(&mut state, id, "state", "cancelling"); }
            Ok(json!(accepted))
        },
        "activate" | "prepare_update" | "abort_update" | "updates.abort" => {
            let mut state = shared.lock().unwrap();
            if method == "prepare_update" {
                if state.tasks.values().any(|t| active(&t.value)) { return Err("请等待正在运行的任务完成后更新".into()); }
                state.maintenance = true;
            } else if method != "activate" { state.maintenance = false; }
            Ok(json!(true))
        },
        "request_close" | "shutdown" | "installer_close" | "finish_update" => {
            {
                let state = shared.lock().unwrap();
                let tasks: Vec<_> = state.tasks.values().filter(|t| active(&t.value)).collect();
                if !tasks.is_empty() && (method != "shutdown" || params["cancel"] != true) { return Err("仍有任务运行，请先结束任务或确认取消".into()); }
                if tasks.iter().any(|t| t.value["cancellable"] != true) { return Err("仍有不可安全中断的写入任务，请等待完成".into()); }
                for task in tasks { task.cancelled.store(true, Ordering::Release); }
            }
            let deadline = Instant::now() + Duration::from_secs(5);
            while shared.lock().unwrap().tasks.values().any(|t| active(&t.value)) {
                if Instant::now() >= deadline { return Err("后台任务尚未退出，请稍后重试".into()); }
                thread::sleep(Duration::from_millis(10));
            }
            closed.store(true, Ordering::Release);
            Ok(json!(true))
        },
        "check_updates" | "updates.check" => Ok(json!(true)),
        "updates.status" => Ok(json!({"checking":false,"error":"Windows 版请退出工具箱后运行新版安装程序；当前自动更新通道仅适用于 macOS。","release":null})),
        value if value.starts_with("updates.") => Err("Windows 版请运行新版安装程序进行更新".into()),
        _ => compatibility(config, method, params),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if let Some(path) = args.windows(2).find(|pair| pair[0] == "--windows-update-helper") {
        if let Err(error) = windows_update::run(std::path::Path::new(&path[1])) {
            eprintln!("Windows 更新未完成：{error}");
            std::process::exit(2);
        }
        return;
    }
    let arg = |name: &str| args.windows(2).find(|pair| pair[0] == name).map(|pair| PathBuf::from(&pair[1]));
    let workspace = arg("--workspace").unwrap_or_else(|| std::env::current_dir().unwrap());
    let config = Config { code_root: arg("--code-root").unwrap_or_else(|| workspace.clone()), workspace,
        python: arg("--python").or_else(|| std::env::var_os("TOOLBOX_PYTHON").map(PathBuf::from)).unwrap_or_else(|| PathBuf::from(if cfg!(windows) { "python" } else { "python3" })) };
    let shared: Shared = Arc::new(Mutex::new(State::default()));
    let closed = AtomicBool::new(false);
    let mut output = io::stdout().lock();
    writeln!(output, "{}", json!({"event":{"kind":"service-ready","protocol":3,"engine":"rust"}})).unwrap(); output.flush().unwrap();
    for line in io::stdin().lock().lines() {
        let Ok(line) = line else { break; };
        let request: Result<Value, _> = serde_json::from_str(&line);
        let row = match request {
            Ok(request) if request.is_object() && request["method"].is_string() => {
                let result = call(&config, &shared, &closed, request["method"].as_str().unwrap(), request.get("params").unwrap_or(&json!({})));
                match result { Ok(result) => json!({"id":request.get("id"),"result":result}), Err(error) => json!({"id":request.get("id"),"error":redact(&error)}) }
            },
            _ => json!({"id":null,"error":"请求格式无效"}),
        };
        if writeln!(output, "{row}").is_err() || output.flush().is_err() { break; }
        if closed.load(Ordering::Acquire) { break; }
    }
    // Losing the host never interrupts an in-flight workbook write.
    loop {
        let state = shared.lock().unwrap();
        let tasks: Vec<_> = state.tasks.values().filter(|t| active(&t.value)).collect();
        if tasks.is_empty() { break; }
        for task in tasks { if task.value["cancellable"] == true { task.cancelled.store(true, Ordering::Release); } }
        drop(state); thread::sleep(Duration::from_millis(20));
    }
    let _ = writeln!(output, "{}", json!({"event":{"kind":"service-closed"}}));
    let _ = output.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn successful_snapshot_message_is_redacted_like_events() {
        let shared: Shared = Arc::new(Mutex::new(State::default()));
        shared.lock().unwrap().tasks.insert("task".into(), Task {
            value: json!({"state":"running"}),
            cancelled: Arc::new(AtomicBool::new(false)),
        });
        transition(&shared, "task", "succeeded", json!({"message":"password=synthetic-secret"}), None);
        let state = shared.lock().unwrap();
        assert_eq!(state.tasks["task"].value["message"], "password=[已隐藏]");
    }

    #[test]
    fn accepted_cancellation_cannot_be_overwritten_by_completion() {
        let shared: Shared = Arc::new(Mutex::new(State::default()));
        shared.lock().unwrap().tasks.insert("task".into(), Task {
            value: json!({"state":"cancelling","cancellable":true}),
            cancelled: Arc::new(AtomicBool::new(true)),
        });
        transition(&shared, "task", "succeeded", json!({"message":"done"}), None);
        assert_eq!(shared.lock().unwrap().tasks["task"].value["state"], "cancelled");
    }

    #[test]
    fn failed_exclusive_create_never_deletes_somebody_elses_request() {
        let id = format!("existing-request-test-{}", NEXT.fetch_add(1, Ordering::Relaxed));
        let path = std::env::temp_dir().join(format!("toolbox-native-task-{}-{id}.json", std::process::id()));
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&path).unwrap();
        file.write_all(b"existing-request").unwrap(); drop(file);
        let shared: Shared = Arc::new(Mutex::new(State::default()));
        let cancelled = Arc::new(AtomicBool::new(false));
        shared.lock().unwrap().tasks.insert(id.clone(), Task { value: json!({"state":"created"}), cancelled:cancelled.clone() });
        let config = Config { workspace:std::env::temp_dir(), code_root:std::env::temp_dir(), python:PathBuf::from("never-run") };
        run_worker(config, shared, id, json!({}), json!({}), cancelled);
        let content = fs::read(&path);
        let _ = fs::remove_file(path);
        assert_eq!(content.unwrap(), b"existing-request");
    }
}
