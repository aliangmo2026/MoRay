import sys

def divide(a, b):
    if b == 0:
        return None
    return a / b

def main():
    print("demo", divide(6, 0))
    return 0

if __name__ == "__main__":
    sys.exit(main())