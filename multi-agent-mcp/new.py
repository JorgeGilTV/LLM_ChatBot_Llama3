list1 = [2,3,6,8,6,8,3,7,3]
count=0
number={}
print(len(list1))

    #print (range(list1))
for l1 in list1:
    #print("value:",l1)
    count=0
    for i in range(len(list1)):
        print ("value",list1[i],l1)
        if ( l1 == list1[i] ):
               count=count+1
    number.append(count)
print(number)
for n in number:
    if n > 

