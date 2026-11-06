"""Local-only resources for timetable; no containers and no startup model request."""
from pathlib import Path
import secrets
from reminder_service import ProjectLock
from timetable_store import TimetableStore
from timetable_assets import TimetableAssets
from timetable_service import TimetableService,TimetableController
from timetable_reminders import TaskReminderBridge
from timetable_routes import create_timetable_routes


class TimetableRuntime:
    def __init__(self,project_dir,*,origin,now,vision_factory,settings,reminder_service):
        self.root=Path(project_dir);self.reminders=reminder_service;self.closed=False
        private=self.root/'.private';private.mkdir(parents=True,exist_ok=True)
        self.lock=ProjectLock(private/'timetable-writer.lock')
        if not self.lock.acquire():raise ValueError('另一个进程正在管理本机课表。')
        try:
            secret_path=private/'timetable-preview.key'
            try:
                with secret_path.open('xb') as stream:stream.write(secrets.token_bytes(32))
            except FileExistsError:pass
            secret=secret_path.read_bytes()
            if len(secret)!=32:raise ValueError('课表预览凭据无效，请核对私有目录。')
            data=self.root/'data/timetables'
            store=TimetableStore(data/'timetables.sqlite3')
            assets=TimetableAssets(data,secret,schedule_is_live=store.is_live);assets.cleanup_staging()
            self.service=TimetableService(store,assets,now)
            if reminder_service is not None:
                bridge=TaskReminderBridge(self.service,reminder_service,self.service.coordinator)
                self.service.reminder_bridge=bridge;bridge.recover()
                reminder_service.delivery_adapter=bridge.prepare_delivery
            self.controller=TimetableController(self.service,settings,vision_factory)
            self.router=create_timetable_routes(assets,assets.resolve_access)
        except BaseException:
            self.lock.release()
            raise

    def close(self):
        if not self.closed:
            if self.reminders:self.reminders.delivery_adapter=None
            self.service.store.close();self.lock.release();self.closed=True
