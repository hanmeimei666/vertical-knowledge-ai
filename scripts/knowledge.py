"""Compatibility API for historical course checks. Generic CLI: platform_cli.py."""
import legacy_course as legacy
KB, COURSE = legacy.KB, legacy.COURSE
read = legacy.read

def search(query, limit=5):
    legacy.KB = KB
    return legacy.search(query, limit)

def verify():
    legacy.KB, legacy.COURSE = KB, COURSE
    return legacy.verify()

if __name__ == '__main__':
    raise SystemExit(legacy.main())
