import time
from datetime import datetime, timezone

def check_python_timezone():
    print("=== time module info ===")
    print("time.tzname:", time.tzname)            # Tuple of (standard, daylight)
    print("time.timezone (offset seconds):", time.timezone)
    print("time.daylight (1 if DST active):", time.daylight)
    print("time.localtime():", time.localtime())
    
    print("\n=== datetime module info ===")
    now_naive = datetime.now()
    now_utc = datetime.utcnow()
    now_aware_local = datetime.now().astimezone()
    now_aware_utc = datetime.now(timezone.utc)
    
    print("datetime.now() (naive):", now_naive, "(no tzinfo)")
    print("datetime.utcnow() (naive):", now_utc, "(no tzinfo)")
    print("datetime.now().astimezone() (aware local):", now_aware_local)
    print("datetime.now(timezone.utc) (aware UTC):", now_aware_utc)

if __name__ == "__main__":
    check_python_timezone()

