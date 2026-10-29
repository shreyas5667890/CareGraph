def find_missing(n):
    num=set(n)
    for i in range(1,len(n)+1):
        if i not in num:
            return i

n=[1,2,4,5,6,7,8]
print(find_missing(n))