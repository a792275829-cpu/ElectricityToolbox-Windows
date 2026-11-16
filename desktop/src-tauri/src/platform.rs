use std::process::Command;

pub fn bundled_python() -> &'static str {
    "python/python.exe"
}

pub fn venv_python() -> &'static str {
    ".venv/Scripts/python.exe"
}

pub fn bundled_node() -> &'static str {
    "node/node.exe"
}

pub fn setup_script() -> &'static str {
    "setup_windows.ps1"
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
