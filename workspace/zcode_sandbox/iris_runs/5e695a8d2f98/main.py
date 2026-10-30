x = 10
y = 0
try:
    z = x / y
except ZeroDivisionError as e:
    print("caught:", e)
print("done")