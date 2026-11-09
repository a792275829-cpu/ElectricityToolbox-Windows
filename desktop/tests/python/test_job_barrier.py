import sys, unittest, os, threading, time, subprocess
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '电力工具箱')]
if os.name == "nt":
    from toolbox.tasks.windows_process import ProcessJob

@unittest.skipUnless(os.name == "nt", "Windows job ownership")
class JobBarrierTests(unittest.TestCase):
    def test_wait_retries_delayed_owned_handles_and_preserves_job_on_timeout(self):
        job = object.__new__(ProcessJob)
        job.handle = 123
        with patch.object(job, '_owned_process_handles', return_value=[456]), patch('toolbox.tasks.windows_process.kernel.WaitForSingleObject', return_value=258), patch('toolbox.tasks.windows_process.kernel.CloseHandle') as close:
            with self.assertRaises(TimeoutError): job.wait_empty(0)
            self.assertEqual(job.handle, 123)
            close.assert_called_once_with(456)
        with patch.object(job, '_owned_process_handles', return_value=[456]), patch('toolbox.tasks.windows_process.kernel.WaitForSingleObject', side_effect=[258, 0]), patch('toolbox.tasks.windows_process.kernel.CloseHandle'):
            job.wait_empty(.2)
            self.assertEqual(job.handle, 123)
    def test_termination_error_preserves_job_and_captured_handles(self):
        job = object.__new__(ProcessJob)
        job.handle = 123
        job._cleanup_handles = [456]
        with patch.object(job, '_capture_for_cleanup'), patch('toolbox.tasks.windows_process.kernel.TerminateJobObject', return_value=0), patch('toolbox.tasks.windows_process.kernel.CloseHandle') as close:
            with self.assertRaises(OSError):
                job.terminate()
            self.assertEqual(job.handle, 123)
            self.assertEqual(job._cleanup_handles, [456])
            close.assert_not_called()

    def test_recapture_closes_duplicates_without_growing_retained_handles(self):
        job = object.__new__(ProcessJob)
        job.handle = 123
        job._cleanup_handles = [456]
        with patch.object(job, '_owned_process_handles', side_effect=lambda: [457]), patch('toolbox.tasks.windows_process.kernel.GetProcessId', return_value=42), patch('toolbox.tasks.windows_process.kernel.CloseHandle') as close:
            for _ in range(100):
                job._capture_for_cleanup()
            self.assertEqual(job._cleanup_handles, [456])
            self.assertEqual(close.call_count, 100)
            close.assert_called_with(457)

    def test_timeout_keeps_captured_process_handles_for_retry(self):
        job = object.__new__(ProcessJob)
        job.handle = 123
        job._cleanup_handles = [456]
        with patch.object(job, '_owned_process_handles', return_value=[]), patch('toolbox.tasks.windows_process.kernel.GetProcessId', return_value=42), patch('toolbox.tasks.windows_process.kernel.WaitForSingleObject', return_value=258), patch('toolbox.tasks.windows_process.kernel.CloseHandle') as close:
            with self.assertRaises(TimeoutError):
                job.wait_empty(0)
            self.assertEqual(job._cleanup_handles, [456])
            close.assert_not_called()
        with patch.object(job, '_owned_process_handles', return_value=[]), patch('toolbox.tasks.windows_process.kernel.GetProcessId', return_value=42), patch('toolbox.tasks.windows_process.kernel.WaitForSingleObject', return_value=0), patch('toolbox.tasks.windows_process.kernel.CloseHandle'):
            job.wait_empty(.1)

    def test_runner_retains_ownership_until_delayed_barrier_confirms(self):
        from toolbox.tasks import ProcessRunner, CancellationToken
        runner = ProcessRunner(terminate_timeout=.05)
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        original = ProcessJob.wait_empty
        attempts = []
        def delayed(job, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                entered.set()
                release.wait(3)
                raise TimeoutError("deterministic delayed exit")
            return original(job, timeout)
        def run():
            runner.run([sys.executable, '-c', 'pass'], cwd=Path.cwd(), env=None,
                       token=CancellationToken(), on_output=lambda _: None)
            completed.set()
        with patch.object(ProcessJob, 'wait_empty', delayed):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(completed.is_set())
                self.assertIsNotNone(runner.active_process)
            finally:
                release.set()
                worker.join(5)
        self.assertTrue(completed.is_set())
        self.assertGreaterEqual(len(attempts), 2)
        self.assertIsNone(runner.active_process)

    def test_real_independent_stdio_descendant_fast_exit_and_cancel(self):
        from toolbox.tasks import ProcessRunner, CancellationToken, TaskCancelled
        from toolbox_platform import process_is_alive
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                token, pids = CancellationToken(), []
                def output(line):
                    if line.startswith('pid:'):
                        pids.append(int(line.split(':')[1]))
                        if cancel:
                            token.cancel()
                code = ('import subprocess,sys,time; '
                        'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],'
                        'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                        'print("pid:"+str(p.pid),flush=True); ' + ('time.sleep(60)' if cancel else ''))
                runner = ProcessRunner(terminate_timeout=.5)
                if cancel:
                    with self.assertRaises(TaskCancelled):
                        runner.run([sys.executable,'-u','-c',code],cwd=Path.cwd(),env=None,token=token,on_output=output)
                else:
                    runner.run([sys.executable,'-u','-c',code],cwd=Path.cwd(),env=None,token=token,on_output=output)
                self.assertEqual(len(pids), 1)
                self.assertFalse(process_is_alive(pids[0]))
                self.assertIsNone(runner.active_process)
if __name__ == '__main__': unittest.main()


