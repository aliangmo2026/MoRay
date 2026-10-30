"""FizzBuzz: 打印 1 到 n，3 的倍数输出 Fizz，5 的倍数输出 Buzz，共同倍数输出 FizzBuzz。"""


def fizzbuzz(n):
    """返回 1..n 的 FizzBuzz 结果列表。"""
    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return result


if __name__ == "__main__":
    for line in fizzbuzz(100):
        print(line)
