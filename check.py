import requests, json
r = requests.get('http://127.0.0.1:8080/api/state')
d = r.json()
s = d.get('state', {})
print(f"grab_time = {s.get('grab_hour')}:{s.get('grab_minute')}:{s.get('grab_second')}")
print(f"status = {s.get('status')}")
print(f"next_grab = {s.get('next_grab')}")
sync = d.get('sync', {})
print(f"sync_source = {sync.get('source')}")
print(f"sync_samples = {sync.get('samples')}")
print(f"sync_offset = {sync.get('offset_ms')}")

# Also check scheduler
try:
    from main import _scheduler
    if _scheduler:
        jobs = _scheduler.get_jobs()
        for j in jobs:
            print(f"Job: {j.id} next_run={j.next_run_time} trigger={j.trigger}")
    else:
        print("Scheduler is None!")
except Exception as e:
    print(f"Scheduler check error: {e}")
