"""洪湖专题通报聚合（手册 7.13 /honghu/latest、/honghu/mini）+ SSE 实时推送"""
import asyncio
import json
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import COMPLETED_STATUS
from ..database import get_db
from ..models import WorkOrder, ExtensionOrder, H5Complaint, H5Receipt, Task, Notice
from ..deps import get_current_user

router = APIRouter(tags=["honghu"])

_event_queue: list[str] = []


def push_event(event: str):
    _event_queue.append(event)


@router.get("/honghu/latest")
def honghu_latest(user=Depends(get_current_user), db: Session = Depends(get_db)):
    month = datetime.now().strftime("%Y-%m")
    total = db.query(WorkOrder).filter(func.substr(WorkOrder.accept_time, 1, 7) == month).count()
    done = db.query(WorkOrder).filter(func.substr(WorkOrder.accept_time, 1, 7) == month,
                                      WorkOrder.status.in_(COMPLETED_STATUS)).count()
    ext = db.query(func.sum(ExtensionOrder.income)).filter(ExtensionOrder.month == month).scalar() or 0
    comp = db.query(H5Complaint).filter(func.substr(H5Complaint.accept_time, 1, 7) == month).count()
    return {"period": month, "sections": [
        {"title": "竣工率概况", "lines": [f"本月受理 {total} 单，已施工 {done} 单。",
                                      f"整体竣工率 {round(done / total * 100, 2) if total else 0}%（个人口径见竣工率页）。"]},
        {"title": "延伸服务", "lines": [f"本月延伸服务收入 {ext:.1f} 元。"]},
        {"title": "H5 盯控", "lines": [f"本月投诉工单 {comp} 单，H5 收取情况见 H5 短信盯控页。"]},
    ]}


@router.get("/honghu/mini")
def honghu_mini(user=Depends(get_current_user), db: Session = Depends(get_db)):
    month = datetime.now().strftime("%Y-%m")
    return {"period": month,
            "open_tasks": db.query(Task).filter(Task.status.in_(["assigned", "accepted", "submitted"])).count(),
            "latest_notice": db.query(Notice).filter(Notice.status == "published").order_by(
                Notice.id.desc()).first().title if db.query(Notice).filter(Notice.status == "published").first() else ""}


@router.get("/events/stream")
async def event_stream():
    """SSE 实时推送（手册 7.13）：数据更新后看板自动刷新"""
    async def gen():
        idx = 0
        while True:
            if idx < len(_event_queue):
                ev = _event_queue[idx]
                idx += 1
                yield f"data: {json.dumps({'event': ev}, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps({'event': 'ping', 'time': datetime.now().isoformat()})}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
