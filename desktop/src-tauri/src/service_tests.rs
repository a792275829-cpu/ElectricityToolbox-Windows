use super::*;

#[test]
fn reopening_a_closed_ui_reports_pending_cleanup_without_replaying_tasks() {
    let state = State {
        backend: Err("unused".into()), ui: Err("Python 任务服务已退出".into()),
        native_closed: AtomicBool::new(true),
    };
    let error = state.ensure_ui_open().unwrap_err();
    assert!(error.contains("后台正在结束任务并清理"));
    assert!(error.contains("不要重复写入"));
    state.native_closed.store(false, Ordering::Release);
    assert!(state.ensure_ui_open().is_ok());
}

#[test]
fn real_python_protocol_starts_without_a_gui_and_shuts_down_cleanly() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap().parent().unwrap();
    let venv = root.join(platform::venv_python());
    let python = if venv.exists() { venv } else { PathBuf::from(if cfg!(windows) { "python" } else { "python3" }) };
    let folder = tempfile::tempdir().unwrap();
    let mut child = Command::new(python)
        .arg("-B").arg(root.join("desktop/python/service_bridge.py"))
        .arg("--workspace").arg(folder.path())
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().unwrap();
    {
        let mut input = child.stdin.take().unwrap();
        input.write_all(b"{\"id\":1,\"method\":\"ping\"}\n{\"id\":2,\"method\":\"shutdown\"}\n").unwrap();
    }
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let rows: Vec<Value> = String::from_utf8(output.stdout).unwrap().lines()
        .map(|line| serde_json::from_str(line).unwrap()).collect();
    let reply = rows.iter().find(|row| row["id"] == 1).unwrap();
    assert_eq!(reply["result"]["protocol"], 3);
    assert_eq!(reply["result"]["ui"], "headless");
    assert_eq!(rows.last().unwrap()["event"]["kind"], "service-closed");
}

#[test]
fn backend_reports_a_dead_worker_without_writing_to_its_pipe() {
    let (shell, args) = if cfg!(windows) { ("cmd.exe", ["/c", "exit 7"]) } else { ("/bin/sh", ["-c", "exit 7"]) };
    let mut child = Command::new(shell).args(args)
        .stdin(Stdio::piped()).spawn().unwrap();
    let input = child.stdin.take().unwrap();
    child.wait().unwrap();
    let backend = Backend { input: Arc::new(Mutex::new(input)), child: Mutex::new(child),
        pending: Arc::new(Mutex::new(HashMap::new())), next_id: AtomicU64::new(1) };
    assert_eq!(backend.request("start", json!({})).unwrap_err(), "Python 任务服务已退出");
    assert!(backend.pending.lock().unwrap().is_empty());
}
