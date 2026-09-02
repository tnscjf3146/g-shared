"""Phase 1 실습 — 해시, 해시 체인, 머클 트리. 표준 라이브러리만."""
import hashlib


def h(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


# ── ① 눈사태 효과 ─────────────────────────────────────────
print("① 눈사태 효과")
print("  hello  →", h("hello"))
print("  hellp  →", h("hellp"))
print("  1글자 차이인데 공통점이 없다.\n")


# ── ② 해시 체인 ───────────────────────────────────────────
def build_chain(datas):
    chain, prev = [], "0" * 64            # 제네시스의 이전 해시는 0
    for d in datas:
        block = {"data": d, "prev": prev}
        block["hash"] = h(block["data"] + block["prev"])
        chain.append(block)
        prev = block["hash"]
    return chain


def verify(chain):
    """각 블록의 hash가 재계산값과 같고, prev가 앞 블록 hash와 같은지."""
    for i, b in enumerate(chain):
        if b["hash"] != h(b["data"] + b["prev"]):
            return f"블록 {i}: 데이터가 해시와 안 맞음"
        if i > 0 and b["prev"] != chain[i - 1]["hash"]:
            return f"블록 {i}: 이전 해시가 끊어짐"
    return "OK"


chain = build_chain([f"tx{i}" for i in range(10)])
print("② 해시 체인 10칸")
print("  검증:", verify(chain))

chain[3]["data"] = "tx3-위조"               # 중간 블록 하나 조작
print("  블록 3 조작 후:", verify(chain))

chain[3]["hash"] = h(chain[3]["data"] + chain[3]["prev"])   # 해시까지 다시 계산해서 숨겨봄
print("  블록 3 해시도 재계산:", verify(chain))
print("  → 블록 3은 멀쩡해 보이지만 블록 4가 든 '이전 해시'가 안 맞아 걸린다.")
print("  → 숨기려면 4~9 전부 재계산해야 한다. 마지막 해시 하나가 전체 역사를 고정한다.\n")


# ── ③ 머클 트리 ───────────────────────────────────────────
def merkle_root(leaves):
    level = [h(x) for x in leaves]
    while len(level) > 1:
        if len(level) % 2:                # 홀수면 마지막을 복제
            level.append(level[-1])
        level = [h(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_proof(leaves, idx):
    """idx번 리프가 루트에 포함됨을 증명하는 형제 해시 목록."""
    level, proof = [h(x) for x in leaves], []
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = idx ^ 1                     # 형제 인덱스 (짝수↔홀수)
        proof.append((level[sib], sib < idx))   # (형제해시, 형제가 왼쪽인가)
        level = [h(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_proof(leaf, proof, root):
    cur = h(leaf)
    for sib, sib_is_left in proof:
        cur = h(sib + cur) if sib_is_left else h(cur + sib)
    return cur == root


results = [f"vector{i}" for i in range(1000)]    # 프로젝트: 결과 1,000건
root = merkle_root(results)
print("③ 머클 트리 — 결과 1,000건")
print("  루트 (온체인에 올릴 32바이트):", root[:16] + "...")

proof = merkle_proof(results, 537)
print(f"  537번 항목의 증명 크기: {len(proof)}개 해시  (1,000건인데 log2 ≈ 10)")
print("  537번 검증:", verify_proof("vector537", proof, root))
print("  537번 위조 검증:", verify_proof("vector537-위조", proof, root))
print("  → 검증자는 1,000건 전부 없어도, 루트 + 해시 10개로 한 건을 확인한다.")

assert verify(build_chain(["a", "b"])) == "OK"
assert verify_proof("vector0", merkle_proof(results, 0), root)
