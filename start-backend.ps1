Set-Location "$PSScriptRoot\backend"
$env:CELERY_TASK_ALWAYS_EAGER = "true"
$env:DOCUMENT_TASK_LOCK_BACKEND = "memory"
py -3.10 -m pip install -r requirements.txt
py -3.10 manage.py migrate
py -3.10 manage.py bootstrap_demo
py -3.10 manage.py runserver 127.0.0.1:8000
