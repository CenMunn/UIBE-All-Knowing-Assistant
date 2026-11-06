"""Stable column ordering with the same index mapping for every recognition row."""
from copy import deepcopy
from dataclasses import replace
from timetable_models import RecognitionDraft


def order_recognition(draft: RecognitionDraft) -> RecognitionDraft:
    records=deepcopy(draft.records)
    order=sorted(range(len(records)),key=lambda i:tuple(
        records[i].get(key) if records[i].get(key) is not None else 999
        for key in ('weekday','start_period','end_period'))+(i,))
    mapping={old:new for new,old in enumerate(order)}
    issues=deepcopy(draft.issues)
    for issue in issues:
        if issue.get('record_index') is not None:issue['record_index']=mapping[issue['record_index']]
    return replace(draft,records=tuple(records[i] for i in order),coverage=deepcopy(draft.coverage),issues=issues)
