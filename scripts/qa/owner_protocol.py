"""Reusable QA adapter for the real compiled owner stdin/stdout protocol.
No HTTP bridge, mocked responses, auth copying, or debug listener.
"""
from __future__ import annotations
import hashlib,json,os,queue,subprocess,threading,time
from pathlib import Path
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake

class CompiledOwner:
    def __init__(self, executable: Path, database: Path, qa_root: Path, *, resume=False):
        self.exe=executable.resolve(strict=True);self.db=database.resolve();self.qa_root=qa_root.resolve(strict=True)
        if not self.db.is_relative_to(self.qa_root):raise ValueError('Database must stay within explicitly owned QA root')
        if self.db.exists() and not resume:raise ValueError('Existing database requires explicit resume=True')
        self.db.parent.mkdir(parents=True,exist_ok=True);self.receipts=[]
    def __enter__(self):
        env=dict(os.environ);env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
        self.process=subprocess.Popen([str(self.exe)],cwd=self.db.parent,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW)
        self.lines=queue.Queue();self.thread=threading.Thread(target=self._read,daemon=True);self.thread.start()
        handshake=LaunchHandshake();credential=handshake.issue()
        self._send(dict(type='launch',secret_hex=credential.secret_hex,database_path=str(self.db),owner_id='programmatic-qa-'+str(self.process.pid)))
        ready=self.lines.get(timeout=240)
        if not ready or not ready.get('ok'):raise RuntimeError('Owner launch rejected')
        handshake.verify(ready['response_hex'])
        self.identity=dict(pid=self.process.pid,executable=str(self.exe),sha256=hashlib.sha256(self.exe.read_bytes()).hexdigest(),database=str(self.db),transport='private stdin/stdout plus production handshake')
        return self
    def _read(self):
        for line in self.process.stdout:self.lines.put(json.loads(line))
        self.lines.put(None)
    def _send(self,message):
        self.process.stdin.write(json.dumps(message)+'\n');self.process.stdin.flush()
    def request(self,method,params=None,*,timeout=1800):
        request_id='qa-'+str(len(self.receipts));started=time.monotonic();self._send(dict(id=request_id,method=method,params=params or {}))
        while True:
            reply=self.lines.get(timeout=timeout)
            if reply is None:raise RuntimeError('Owned process ended before reply')
            if reply.get('id')==request_id:break
        self.receipts.append(dict(method=method,params=params or {},elapsed_seconds=round(time.monotonic()-started,3),reply=reply))
        if not reply.get('ok'):raise RuntimeError(reply.get('error',{}))
        return reply['result']
    def __exit__(self,*args):
        self.process.stdin.close()
        try:self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # Only this adapter's exact owned child; never enumerate/terminate foreign processes.
            self.process.terminate();self.process.wait(timeout=10)
        self.thread.join(timeout=2);self.identity['closed_exit_code']=self.process.returncode
