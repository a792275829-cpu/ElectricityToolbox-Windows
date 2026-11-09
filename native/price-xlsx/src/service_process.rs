//! Own only this worker's process tree. Never run child code before job assignment.
use std::io;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};

pub struct OwnedChild {
    pub child: Child,
    #[cfg(windows)]
    job: windows::Job,
}

impl OwnedChild {
    pub fn spawn_if_active(command: &mut Command, cancelled: &AtomicBool, allow_browser_breakaway: bool) -> io::Result<Self> {
        if cancelled.load(Ordering::Acquire) { return Err(io::Error::new(io::ErrorKind::Interrupted, "任务已取消")); }
        command.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000 | 0x00000004);
            let mut child = command.spawn()?;
            match windows::Job::assign_and_resume(&child, cancelled, allow_browser_breakaway) {
                Ok(job) => Ok(Self { child, job }),
                Err(error) => { let _ = child.kill(); let _ = child.wait(); Err(error) }
            }
        }
        #[cfg(unix)]
        {
            let _ = allow_browser_breakaway;
            use std::os::unix::process::CommandExt;
            command.process_group(0);
            Ok(Self { child: command.spawn()? })
        }
    }

    pub fn cleanup(&mut self, timeout: std::time::Duration) -> io::Result<()> {
        #[cfg(windows)]
        { self.job.terminate()?; self.job.wait_empty(timeout)?; }
        #[cfg(unix)]
        { let _ = timeout; self.terminate()?; }
        self.child.wait()?;
        Ok(())
    }
    pub fn terminate(&mut self) -> io::Result<()> {
        #[cfg(windows)]
        return self.job.terminate();
        #[cfg(unix)]
        unsafe {
            unsafe extern "C" { fn kill(pid: i32, signal: i32) -> i32; }
            kill(-(self.child.id() as i32), 9);
            Ok(())
        }
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        while let Err(error) = self.cleanup(std::time::Duration::from_secs(2)) {
            eprintln!("后台进程清理尚未确认，保留所有权并重试：{error}");
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
}

pub fn retry_cleanup(mut attempt: impl FnMut() -> io::Result<()>, mut report: impl FnMut(&io::Error)) {
    while let Err(error) = attempt() {
        report(&error);
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}

pub fn hide_console(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    #[cfg(not(windows))]
    let _ = command;
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use std::sync::atomic::{AtomicBool, Ordering};

    type Handle = *mut c_void;
    #[repr(C)]
    #[derive(Default)]
    struct BasicLimits {
        process_time: i64, job_time: i64, flags: u32,
        min_working_set: usize, max_working_set: usize, active_limit: u32,
        affinity: usize, priority: u32, scheduling: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimits {
        basic: BasicLimits, io: [u64; 6], process_memory: usize, job_memory: usize,
        peak_process_memory: usize, peak_job_memory: usize,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ThreadEntry {
        size: u32, usage: u32, thread_id: u32, process_id: u32,
        base_priority: i32, delta_priority: i32, flags: u32,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(job: Handle, class: i32, data: *const c_void, length: u32) -> i32;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
        fn TerminateJobObject(job: Handle, exit_code: u32) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
        fn QueryInformationJobObject(job: Handle, class: i32, data: *mut c_void, length: u32, returned: *mut u32) -> i32;
        fn GetProcessId(process: Handle) -> u32;
        fn OpenProcess(access: u32, inherit: i32, id: u32) -> Handle;
        fn IsProcessInJob(process: Handle, job: Handle, result: *mut i32) -> i32;
        fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
        fn CreateToolhelp32Snapshot(flags: u32, process: u32) -> Handle;
        fn Thread32First(snapshot: Handle, entry: *mut ThreadEntry) -> i32;
        fn Thread32Next(snapshot: Handle, entry: *mut ThreadEntry) -> i32;
        fn OpenThread(access: u32, inherit: i32, id: u32) -> Handle;
        fn ResumeThread(thread: Handle) -> u32;
    }

    pub struct Job(usize, std::sync::Mutex<Vec<usize>>);
    impl Job {
        pub fn assign_and_resume(child: &Child, cancelled: &AtomicBool, allow_browser_breakaway: bool) -> io::Result<Self> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() { return Err(io::Error::last_os_error()); }
                let job = Self(handle as usize, std::sync::Mutex::new(Vec::new()));
                let mut limits = ExtendedLimits::default();
                limits.basic.flags = 0x2000;
                // WPS owns its persistent browser via a dedicated profile/PID.
                // Ordinary descendants still inherit this kill-on-close job;
                // only an explicit CREATE_BREAKAWAY_FROM_JOB launch escapes.
                if allow_browser_breakaway { limits.basic.flags |= 0x0800; }
                if SetInformationJobObject(handle, 9, &limits as *const _ as _, std::mem::size_of_val(&limits) as u32) == 0
                    || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0 {
                    return Err(io::Error::last_os_error());
                }
                if cancelled.load(Ordering::Acquire) { return Err(io::Error::new(io::ErrorKind::Interrupted, "任务已取消")); }
                let snapshot = CreateToolhelp32Snapshot(4, 0);
                if snapshot as isize == -1 { return Err(io::Error::last_os_error()); }
                let result = (|| {
                    let mut entry = ThreadEntry { size: std::mem::size_of::<ThreadEntry>() as u32, ..Default::default() };
                    let mut found = Thread32First(snapshot, &mut entry);
                    let mut resumed = false;
                    while found != 0 {
                        if entry.process_id == child.id() {
                            let thread = OpenThread(2, 0, entry.thread_id);
                            if thread.is_null() { return Err(io::Error::last_os_error()); }
                            let previous = ResumeThread(thread);
                            CloseHandle(thread);
                            if previous == u32::MAX { return Err(io::Error::last_os_error()); }
                            resumed |= previous > 0;
                        }
                        entry.size = std::mem::size_of::<ThreadEntry>() as u32;
                        found = Thread32Next(snapshot, &mut entry);
                    }
                    if resumed { Ok(()) } else { Err(io::Error::other("无法恢复后台任务的初始线程")) }
                })();
                CloseHandle(snapshot);
                result?;
                Ok(job)
            }
        }
        fn capture(&self) -> io::Result<()> {
            let mut capacity = 64usize;
            loop {
                let mut list = vec![0usize; capacity + 1];
                let ok = unsafe { QueryInformationJobObject(self.0 as Handle, 3, list.as_mut_ptr() as _, (list.len() * std::mem::size_of::<usize>()) as u32, std::ptr::null_mut()) };
                if ok == 0 {
                    let error = io::Error::last_os_error();
                    if error.raw_os_error() == Some(234) { capacity *= 2; continue; }
                    return Err(error);
                }
                let count = unsafe { *((list.as_ptr() as *const u32).add(1)) } as usize;
                for pid in &list[1..1 + count] {
                    let process = unsafe { OpenProcess(0x00100000 | 0x1000, 0, *pid as u32) };
                    if process.is_null() {
                        let error = io::Error::last_os_error();
                        if error.raw_os_error() == Some(87) { continue; }
                        return Err(error);
                    }
                    let mut member = 0;
                    if unsafe { IsProcessInJob(process, self.0 as Handle, &mut member) } == 0 {
                        let error = io::Error::last_os_error();
                        unsafe { CloseHandle(process); }
                        return Err(error);
                    }
                    let mut handles = self.1.lock().unwrap();
                    if member != 0 && !handles.iter().any(|handle| unsafe { GetProcessId(*handle as Handle) } == *pid as u32) {
                        handles.push(process as usize);
                    } else { unsafe { CloseHandle(process); } }
                }
                return Ok(());
            }
        }
        pub fn terminate(&self) -> io::Result<()> {
            // Capture on both sides of termination: a job's PID list can remove
            // dying members before their kernel process objects are signalled.
            self.capture()?;
            if unsafe { TerminateJobObject(self.0 as Handle, 1) } == 0 { return Err(io::Error::last_os_error()); }
            self.capture()
        }
        pub fn wait_empty(&self, timeout: std::time::Duration) -> io::Result<()> {
            let deadline = std::time::Instant::now() + timeout;
            loop {
                self.capture()?;
                let mut live = false;
                {
                    let mut handles = self.1.lock().unwrap();
                    let mut retained = Vec::new();
                    let mut error = None;
                    for handle in handles.drain(..) {
                        match unsafe { WaitForSingleObject(handle as Handle, 0) } {
                            0 => { unsafe { CloseHandle(handle as Handle); } },
                            258 => { live = true; retained.push(handle); },
                            _ => { error = Some(io::Error::last_os_error()); retained.push(handle); },
                        }
                    }
                    *handles = retained;
                    if let Some(error) = error { return Err(error); }
                }
                if !live { return Ok(()); }
                if std::time::Instant::now() >= deadline { return Err(io::Error::new(io::ErrorKind::TimedOut, "后台进程清理尚未完成")); }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    }
    impl Drop for Job {
        fn drop(&mut self) {
            for handle in self.1.get_mut().unwrap().drain(..) { unsafe { CloseHandle(handle as Handle); } }
            unsafe { CloseHandle(self.0 as Handle); }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cleanup_retries_before_completion() {
        let mut attempts = 0;
        let mut errors = 0;
        retry_cleanup(|| { attempts += 1; if attempts < 3 { Err(io::Error::new(io::ErrorKind::TimedOut, "delayed")) } else { Ok(()) } }, |_| errors += 1);
        assert_eq!(attempts, 3);
        assert_eq!(errors, 2);
    }
    #[test]
    fn cancelled_before_launch_does_not_start_any_program() {
        let cancelled = AtomicBool::new(true);
        let mut command = Command::new("this-program-must-never-start");
        match OwnedChild::spawn_if_active(&mut command, &cancelled, false) {
            Err(error) => assert_eq!(error.kind(), io::ErrorKind::Interrupted),
            Ok(_) => panic!("cancelled task started a process"),
        }
    }
}
