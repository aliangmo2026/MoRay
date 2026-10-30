def calc_total(price, qty):
    try:
        price = float(price)
        qty = int(qty)
    except (TypeError, ValueError) as exc:
        raise ValueError("price 和 qty 必须可转换为数值") from exc
    return price * qty

if __name__ == "__main__":
    print(calc_total("19.9", "2"))