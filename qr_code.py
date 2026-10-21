"""A small QR encoder for short links (byte mode, error correction M, versions 1-6), so the backend needs no packages."""
from __future__ import annotations

# Version: (error-correction codewords per block, blocks, data codewords per block, alignment pattern centre).
VERSIONS = {1:(10,1,16,None),2:(16,1,28,18),3:(26,1,44,22),4:(18,2,32,26),5:(24,2,43,30),6:(16,4,27,34)}
MASKS = [lambda x,y:(x+y)%2==0,lambda x,y:y%2==0,lambda x,y:x%3==0,lambda x,y:(x+y)%3==0,
         lambda x,y:(x//3+y//2)%2==0,lambda x,y:x*y%2+x*y%3==0,lambda x,y:(x*y%2+x*y%3)%2==0,lambda x,y:((x+y)%2+x*y%3)%2==0]
EXP,LOG = [0]*512,[0]*256
value = 1
for power in range(255):
    EXP[power],LOG[value] = value,power
    value = value<<1^(0x11d if value&0x80 else 0)
for power in range(255,512):EXP[power] = EXP[power-255]


def multiply(a,b):
    return EXP[LOG[a]+LOG[b]] if a and b else 0


def error_correction(data,count):
    generator = [1]
    for power in range(count):
        generator = [a^multiply(b,EXP[power]) for a,b in zip(generator+[0],[0]+generator)]
    remainder = list(data)+[0]*count
    for i in range(len(data)):
        factor = remainder[i]
        if factor:
            for j,g in enumerate(generator):remainder[i+j] ^= multiply(g,factor)
    return remainder[len(data):]


def codewords(raw,version):
    ec,blocks,size,_ = VERSIONS[version]
    bits = '0100'+f'{len(raw):08b}'+''.join(f'{b:08b}' for b in raw)
    capacity = blocks*size*8
    bits += '0'*min(4,capacity-len(bits))
    bits += '0'*(-len(bits)%8)
    data = [int(bits[i:i+8],2) for i in range(0,len(bits),8)]
    data += [(0xec,0x11)[i%2] for i in range(blocks*size-len(data))]
    parts = [data[i*size:(i+1)*size] for i in range(blocks)]
    checks = [error_correction(part,ec) for part in parts]
    return [part[i] for i in range(size) for part in parts]+[check[i] for i in range(ec) for check in checks]


def format_bits(mask):
    data = mask  # Error correction M is 00.
    remainder = data
    for _ in range(10):remainder = remainder<<1^(remainder>>9)*0x537
    return (data<<10|remainder)^0x5412


def penalty(grid):
    size,score = len(grid),0
    lines = [''.join('1' if v else '0' for v in row) for row in grid]
    lines += [''.join(lines[y][x] for y in range(size)) for x in range(size)]
    for line in lines:
        run = 1
        for a,b in zip(line,line[1:]+'x'):
            if a==b:run += 1;continue
            if run>=5:score += run-2
            run = 1
        padded = '0000'+line+'0000'
        score += 40*(padded.count('10111010000')+padded.count('00001011101'))
    score += 3*sum(grid[y][x]==grid[y][x+1]==grid[y+1][x]==grid[y+1][x+1] for y in range(size-1) for x in range(size-1))
    dark = sum(map(sum,grid))
    return score+10*(abs(dark*20-size*size*10)//(size*size))


def matrix(text):
    """Rows of booleans (True is dark) for the text, quiet zone not included."""
    raw = text.encode()
    version = next((v for v,(_,blocks,size,_) in VERSIONS.items() if len(raw)+2<=blocks*size),None)
    if version is None:raise ValueError('二维码内容太长。')
    size = 17+4*version
    grid = [[False]*size for _ in range(size)]
    fixed = [[False]*size for _ in range(size)]
    def put(x,y,dark):grid[y][x],fixed[y][x] = dark,True
    for cx,cy in [(3,3),(size-4,3),(3,size-4)]:
        for dy in range(-4,5):
            for dx in range(-4,5):
                if 0<=cx+dx<size and 0<=cy+dy<size:put(cx+dx,cy+dy,max(abs(dx),abs(dy)) not in (2,4))
    centre = VERSIONS[version][3]
    if centre:
        for dy in range(-2,3):
            for dx in range(-2,3):put(centre+dx,centre+dy,max(abs(dx),abs(dy))!=1)
    for i in range(8,size-8):put(6,i,i%2==0);put(i,6,i%2==0)
    for i in range(9):fixed[8][i] = fixed[i][8] = True
    for i in range(8):fixed[8][size-1-i] = fixed[size-1-i][8] = True
    put(8,size-8,True)
    bits = ''.join(f'{b:08b}' for b in codewords(raw,version))
    places,index = [],0
    # Two-column strips from the right, skipping the vertical timing pattern in column 6.
    for right in [r if r>6 else r-1 for r in range(size-1,0,-2)]:
        for vertical in range(size):
            for column in (right,right-1):
                upward = (right+1)&2==0
                y = size-1-vertical if upward else vertical
                if not fixed[y][column]:
                    grid[y][column] = index<len(bits) and bits[index]=='1';places.append((column,y));index += 1
    best = None
    for mask,rule in enumerate(MASKS):
        candidate = [row[:] for row in grid]
        for x,y in places:
            if rule(x,y):candidate[y][x] = not candidate[y][x]
        info = format_bits(mask)
        bit = lambda i:bool(info>>i&1)
        for i in range(6):candidate[i][8] = bit(i)
        candidate[7][8],candidate[8][8],candidate[8][7] = bit(6),bit(7),bit(8)
        for i in range(9,15):candidate[8][14-i] = bit(i)
        for i in range(8):candidate[8][size-1-i] = bit(i)
        for i in range(8,15):candidate[size-15+i][8] = bit(i)
        score = penalty(candidate)
        if best is None or score<best[0]:best = (score,candidate)
    return best[1]
