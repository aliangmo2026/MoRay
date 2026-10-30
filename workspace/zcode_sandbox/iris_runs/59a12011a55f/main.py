from decimal import Decimal, InvalidOperation


def calc_total(price, qty):
    try:
        price_str = str(price).strip().replace('¥¥', '').replace('$', '')
        price_dec = Decimal(price_str)
        qty_int = int(qty)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('price must be a number, qty must be an int') from exc
    total = price_dec * qty_int
    return total


if __name__ == '__main__':
    print('total:', calc_total('19.9', 3))
    print('total:', calc_total(19.9, 3))