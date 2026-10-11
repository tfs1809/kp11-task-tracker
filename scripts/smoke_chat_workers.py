"""Shared chat quota through two actual Python processes in the browser fixture DB."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
args = parser.parse_args()
if not args.project.startswith('kp11-stage6-browser-'):
    parser.error('Нужен изолированный project после scripts/browser_stage6.py.')
root = Path(__file__).resolve().parents[1]
command = ['docker', 'compose', '-p', args.project, 'exec', '-T', 'web', 'python', 'manage.py', 'shell', '-c']


def run(code):
    result = subprocess.run(command + [code], cwd=root, capture_output=True, text=True, check=True)
    return json.loads(next(line[len('WORKERS:'):] for line in result.stdout.splitlines() if line.startswith('WORKERS:')))


item = run('''import json, uuid
from apps.accounts.models import User
from apps.workitems.services import preview_issue, issue_item
assert User.objects.count() == 4 and not User.objects.exclude(email__startswith='stage6.').exists()
actor=User.objects.get(email='stage6.manager@corp.kp11.ru')
recipient=User.objects.get(email='stage6.first@corp.kp11.ru')
key=uuid.uuid4()
payload=dict(kind='event',title='Изолированная проверка процессов',description='',due_at='2030-01-03T00:00:00+00:00',event_at='2030-01-02T00:00:00+00:00',subtasks=[],users=[str(recipient.pk)],roles=[],groups=[])
kwargs=dict(actor_id=actor.pk,auth_version=actor.auth_version,request_key=key,payload=payload)
preview=preview_issue(**kwargs)
item,_=issue_item(**kwargs,preview_token=preview['token'])
print('WORKERS:'+json.dumps(str(item.pk)))
''')
item = str(uuid.UUID(item))
code = f'''import json, os, uuid
from datetime import datetime, timezone
from unittest.mock import patch
from apps.accounts.models import User
from apps.accounts.limits import RateLimited
from apps.discussions.services import send
actor=User.objects.get(email='stage6.manager@corp.kp11.ru')
counts=dict(pid=os.getpid(),sent=0,limited=0)
# Same fixed minute in both processes avoids an accidental wall-clock boundary;
# buckets, locks, writes, receipts and quotas are real PostgreSQL operations.
with patch('apps.accounts.limits.timezone.now',return_value=datetime(2030,1,2,10,0,5,tzinfo=timezone.utc)):
 for i in range(6):
  try:
   send(actor_id=actor.pk,auth_version=actor.auth_version,item_id=uuid.UUID('{item}'),request_key=uuid.uuid4(),text='Проверка двух процессов')
   counts['sent']+=1
  except RateLimited:
   counts['limited']+=1
print('WORKERS:'+json.dumps(counts))
'''
with ThreadPoolExecutor(max_workers=2) as executor:
    results = list(executor.map(run, [code, code]))
assert len({row['pid'] for row in results}) == 2
assert sum(row['sent'] for row in results) == 10, results
assert sum(row['limited'] for row in results) == 2, results
verified = run(f'''import json
from apps.discussions.models import EventMessage
from apps.workitems.models import MutationReceipt
from apps.audit.models import AuditEntry
item='{item}'
counts=dict(messages=EventMessage.objects.filter(work_item_id=item).count(),receipts=MutationReceipt.objects.filter(work_item_id=item,operation='discussions.send').count(),audit=AuditEntry.objects.filter(object_id=item,action='discussions.sent').count())
assert counts==dict(messages=10,receipts=10,audit=10)
print('WORKERS:'+json.dumps(counts))
''')
print(json.dumps({'run_id': str(uuid.uuid4()), 'workers': results, 'verified': verified}, ensure_ascii=False))
