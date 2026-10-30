def add(a, b):
    return a - b

if __name__ == "__main__":
    result = add(2, 3)
    assert result == 5, f"期望 5，得到 {result}"
    print("OK")
