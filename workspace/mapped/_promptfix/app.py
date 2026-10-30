
import sys

APP = "promptfix"
VERSION = "1.0.0"

def add(a, b):
    return a + b

def sub(a, b):
    return a - b

def mul(a, b):
    return a * b

def divide(a, b):
    if b == 0:
        return None
    return a / b

def describe():
    return "%s %s add=%s sub=%s mul=%s" % (
        APP, VERSION, add(10, 4), sub(10, 4), mul(10, 4)
    )

def main():
    print(describe())
    total = divide(6, 0)
    print("total", total)
    return 0

if __name__ == "__main__":
    sys.exit(main())