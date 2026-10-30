"""Refactored version of D:\\test.py (original left untouched; write to D:\\test.py was denied)."""


def greet() -> None:
    print("hello")


def show_sum() -> None:
    print(123 + 456)


def show_profile(name: str, age: int) -> None:
    print(name, age)


def count_to(end: int) -> None:
    for i in range(1, end + 1):
        print(i)


def read_number(prompt: str) -> int:
    return int(input(prompt))


def read_text(prompt: str) -> str:
    return input(prompt)


def main() -> None:
    greet()
    show_sum()
    show_profile("莫光良", 19)
    count_to(5)

    num = read_number("请输入数字")
    print("你输入的是", num)

    text = read_text("你随便输入点内容:")
    print("你写的: ", text)


if __name__ == "__main__":
    main()
