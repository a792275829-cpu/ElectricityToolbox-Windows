use std::process::Command;

pub fn bundled_python() -> &'static str {
    if cfg!(windows) { "python/python.exe" } else { "python/bin/python3" }
}

pub fn venv_python() -> &'static str {
    if cfg!(windows) { ".venv/Scripts/python.exe" } else { ".venv/bin/python" }
}

pub fn bundled_node() -> &'static str {
    if cfg!(windows) { "node/node.exe" } else { "node/bin/node" }
}

pub fn setup_script() -> &'static str {
    if cfg!(windows) { "setup_windows.ps1" } else { "setup_macos.command" }
}

pub fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW; keep JSON pipes.
    }
    #[cfg(not(windows))]
    let _ = command;
}
