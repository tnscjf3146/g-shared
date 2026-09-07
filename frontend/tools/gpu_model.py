"""3팬 그래픽카드(ASUS PRIME 스타일) 절차 생성기.

실행:  blender -b -P tools/gpu_model.py -- [--blend tools/main_gpu.blend] [--out public/models/gpu.glb] [--render <dir>]

단위: 1 = 100mm. 축: X = 카드 길이(I/O 브라켓이 -X), Y = 카드 높이(PCIe 엣지가 -Y), Z = 두께(팬이 +Z).
glTF 내보내기(+Y up)에서 Z→Y, Y→-Z 로 바뀐다. 웹은 extras.explode 를 (x, z, -y) 로 읽는다.

부품마다 커스텀 프로퍼티:
  explode : 분해 시 이동 벡터(블렌더 좌표)
  order   : 스택 순서(클수록 위 → 먼저 떠오름)
  label   : 라벨 텍스트('' 이면 라벨 없음)
  spin    : 1 이면 로컬 Z 축 회전(팬)
"""
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

# ---------------------------------------------------------------- 인자
argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


BLEND = arg("--blend", "tools/main_gpu.blend")
OUT = arg("--out", "public/models/gpu.glb")
RENDER = arg("--render")

# ---------------------------------------------------------------- 치수
L, H = 3.10, 1.28  # 카드 길이/높이
X0, X1 = -1.55, 1.55
FAN_R, FAN_X = 0.41, (-1.0, 0.0, 1.0)  # 구멍 r=FAN_R+0.02, 링 r=FAN_R+0.03 → 지름 0.88 < 간격 1.0 (겹침 방지)
Z_SHROUD = 0.215  # 쉬라우드 판 중심 (윗면 0.2525)
Z_FAN = 0.155  # 팬을 판보다 깊이 — 블레이드 윗면이 판 윗면보다 ~0.09 아래(FE 식 우물)
Z_FIN = (-0.07, 0.115)  # 바닥은 VRM 브릿지 위, 윗면은 팬 허브 바닥(0.12) 아래
SKIRT_H = 0.425  # 스커트 높이: 판 안쪽 0.01 ~ PCB 윗면(-0.1925)
SKIRT_ZC = Z_SHROUD - 0.1925
Z_PLATE = -0.135  # 콜드플레이트(다이 위). VRM 브릿지는 초크/캡(top -0.102) 위 Z_BRIDGE
Z_BRIDGE = -0.085
Z_PCB = -0.20
Z_BACK = -0.218  # 윗면(-0.208)이 PCB 바닥에 접함 — 조립 상태에서 틈 없음

# ---------------------------------------------------------------- 유틸
def clear():
    bpy.ops.wm.read_homefile(use_empty=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


MATS = {}


def mat(name, color, metallic=0.0, rough=0.5, emit=None, emit_strength=2.0):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    # 노드 이름은 UI 언어에 따라 바뀌므로(한국어: "프린시플드 BSDF") 타입으로 찾는다
    bsdf = next(n for n in m.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if emit:
        bsdf.inputs["Emission Color"].default_value = (*emit, 1)
        bsdf.inputs["Emission Strength"].default_value = emit_strength
    MATS[name] = m
    return m


M = dict(
    shroud=lambda: mat("Shroud", (0.035, 0.036, 0.04), 0.15, 0.42),
    shroud2=lambda: mat("ShroudDark", (0.018, 0.018, 0.02), 0.1, 0.5),
    silver=lambda: mat("Silver", (0.82, 0.83, 0.85), 1.0, 0.22),
    brushed=lambda: mat("Brushed", (0.68, 0.69, 0.71), 1.0, 0.45),  # 윗면 사선 밴드: 브러시드 알루미늄
    lime=lambda: mat("Lime", (0.72, 0.95, 0.05), 0.0, 0.4),  # 로고 라임
    blade=lambda: mat("Blade", (0.03, 0.03, 0.033), 0.05, 0.45),
    hub=lambda: mat("Hub", (0.05, 0.05, 0.055), 0.3, 0.3),
    fin=lambda: mat("Aluminum", (0.62, 0.63, 0.65), 1.0, 0.38),
    copper=lambda: mat("Copper", (0.78, 0.42, 0.22), 1.0, 0.28),
    nickel=lambda: mat("Nickel", (0.55, 0.56, 0.58), 1.0, 0.3),
    pcb=lambda: mat("PCB", (0.008, 0.009, 0.011), 0.0, 0.5),
    trace=lambda: mat("Trace", (0.035, 0.055, 0.025), 0.35, 0.4),  # 마스크 아래 동박 배선 — 기판보다 살짝 밝은 황록
    ic=lambda: mat("IC", (0.012, 0.012, 0.013), 0.15, 0.4),  # 컨트롤러/BIOS IC 검은 몰딩
    via=lambda: mat("Via", (0.32, 0.26, 0.16), 0.9, 0.35),  # 비아 링(노출 동/니켈)
    gold=lambda: mat("Gold", (0.95, 0.72, 0.30), 1.0, 0.25),
    chip=lambda: mat("Chip", (0.012, 0.012, 0.014), 0.2, 0.35),
    die=lambda: mat("Die", (0.75, 0.76, 0.8), 1.0, 0.15),
    cap=lambda: mat("Capacitor", (0.05, 0.05, 0.06), 0.4, 0.3),
    plastic=lambda: mat("Plastic", (0.02, 0.02, 0.022), 0.0, 0.6),
    green=lambda: mat("Accent", (0.46, 0.73, 0.0), 0.0, 0.4, emit=(0.46, 0.73, 0.0)),
)


def tag(o, name, explode=(0, 0, 0), order=0, label="", spin=0):
    o.name = name
    o["explode"] = list(explode)
    o["order"] = order
    o["label"] = label
    o["spin"] = spin
    return o


def link(o):
    bpy.context.scene.collection.objects.link(o)
    return o


def box(name, size, loc, m, bevel=0.0, segs=3, **kw):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.scale = size
    apply_transform(o)
    o.data.materials.append(m)
    if bevel:
        add_bevel(o, bevel, segs)
    return tag(o, name, **kw)


def cone(name, r_bot, r_top, h, loc, m, verts=96):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r_bot, radius2=r_top, depth=h, location=loc)
    o = bpy.context.object
    o.data.materials.append(m)
    return tag(o, name)


def cyl(name, r, h, loc, m, verts=48, rot=(0, 0, 0), **kw):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=h, location=loc, rotation=rot)
    o = bpy.context.object
    o.data.materials.append(m)
    return tag(o, name, **kw)


def torus(name, R, r, loc, m, **kw):
    bpy.ops.mesh.primitive_torus_add(major_radius=R, minor_radius=r, major_segments=64, minor_segments=12, location=loc)
    o = bpy.context.object
    o.data.materials.append(m)
    return tag(o, name, **kw)


def apply_transform(o):
    bpy.ops.object.select_all(action="DESELECT")
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def add_bevel(o, width, segs=3):
    b = o.modifiers.new("Bevel", "BEVEL")
    b.width, b.segments, b.limit_method = width, segs, "ANGLE"
    apply_mods(o)


def apply_mods(o):
    bpy.context.view_layer.objects.active = o
    for md in list(o.modifiers):
        bpy.ops.object.modifier_apply(modifier=md.name)


def cut(o, cutter, solver="EXACT", op="DIFFERENCE"):
    """불리언(기본 차집합). 커터는 하나씩(다중 덩어리 EXACT는 빈 메시를 냄). op="UNION" 이면 합집합."""
    backup = o.data.copy()
    for sv in (solver, "FLOAT"):
        b = o.modifiers.new("Cut", "BOOLEAN")
        b.operation, b.object, b.solver = op, cutter, sv
        b.material_mode = "TRANSFER"  # 합칠 때 커터 재질 유지
        apply_mods(o)
        if len(o.data.polygons) > 0:
            break
        print(f"  !! boolean {op}/{sv} emptied {o.name} — retry", file=sys.stderr)
        o.data = backup.copy()
    bpy.data.objects.remove(cutter, do_unlink=True)


def slab(name, poly, frame, d0, d1, m):
    """임의 평면 위 2D 다각형을 두께로 세운 판. frame=(origin, U, V, N): 점 (u,v) → origin + u·U + v·V, 두께는 N 방향 d0..d1."""
    org, U, V, N = (Vector(f) for f in frame)
    # 다각형을 항상 CCW 로 — CW 면 안팎이 뒤집힌 커터가 되어 차집합이 본체를 지워버린다
    area2 = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))
    if area2 < 0:
        poly = poly[::-1]
    n = len(poly)
    verts = [tuple(org + U * u + V * v + N * d) for d in (d0, d1) for u, v in poly]
    faces = [tuple(range(n))[::-1], tuple(range(n, 2 * n))] + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    o = mesh_from_faces(name, verts, faces, m)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(o.data)
    bm.free()
    return o


def _arc(a0, a1, r, n=40):
    """각도 a0→a1(도) 반지름 r 원호 점열."""
    return [(r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def _cap(cx, cy, r, a0, a1, n=6):
    """(cx,cy) 중심 r 반원/원 점열 — 스피드 라인 둥근 끝."""
    return [(cx + x, cy + y) for x, y in _arc(a0, a1, r, n)]


def engrave_logo_g(o, frame, R, depth=0.012, recess=0.004):
    """G 로고를 frame 평면에 음각+실버 상감. 위: 호+화살촉+스피드 라인, 아래: 호+화살촉+가로 바.
    frame=(origin, U, V, N), N 은 표면 바깥 방향. 다각형 좌표는 단위원 기준(레퍼런스 PNG 트레이스)이라 R 로 스케일.
    각 색은 하나의 단순 다각형 — 조각을 나눠 붙이면 공유 변에서 FLOAT 불리언이 무너져서 통짜로 둔다."""
    ri, ro = 0.61, 1.0
    sc = lambda poly: [(u * R, v * R) for u, v in poly]
    lime = (_arc(36, 170, ro)                                                          # 화살촉 끝 → 바깥 호(시계 반대)
            + [(-0.755, 0.174), (-0.755, 0.097)] + _cap(-1.081, 0.056, 0.041, 90, 270)   # 슬롯1 / 스피드 바1
            + [(-0.684, 0.015), (-0.684, -0.066)] + _cap(-1.122, -0.107, 0.041, 90, 270)  # 슬롯2 / 바2
            + [(-0.786, -0.148), (-0.786, -0.230)] + _cap(-1.040, -0.261, 0.031, 90, 270)  # 슬롯3 / 바3
            + [(-0.525, -0.291)] + _arc(208, 78, ri)                                    # 바닥 평면 → 안쪽 호
            + [(0.327, 0.566), (0.408, 0.658), (0.439, 0.668), (0.439, 0.383), (0.204, 0.301), (0.551, 0.301)])  # 쐐기, 노치 벽, 화살촉 밑변
    dot = _cap(-1.28, -0.107, 0.052, 0, 360, 16)  # 스피드 라인 왼쪽 점 — 바 두께보다 조금 크게
    dark = ([(0.09, 0.097), (1.0, 0.097), (1.0, -0.168)] + _arc(-9.5, -158, ro)         # 바 윗변 → 바깥 호(시계)
            + [(-0.204, -0.332), (-0.418, -0.464), (-0.418, -0.699), (-0.306, -0.546)]   # 화살촉 끝, 노치 벽, 쐐기
            + _arc(-100.5, -16.5, ri) + [(0.592, -0.168), (-0.153, -0.158)])            # 안쪽 호 → 바 밑변 → 챔퍼
    for polys, m in (((lime, dot), M["silver"]()), ((dark,), M["silver"]())):  # 위아래 모두 실버 상감(색 없이 음영만)
        for poly in polys:
            cut(o, slab("_lg", sc(poly), frame, -depth, 0.01, o.data.materials[0]))  # EXACT — FLOAT 는 작은 점 커터를 껍질로 남김
        for poly in polys:
            fuse(o, slab("_li", sc(poly), frame, -depth + 0.0005, -recess, m))


def fuse(o, part):
    """part 를 o 에 EXACT 합집합으로 녹여 한 덩어리로(겹치는 면 없음). part 재질 유지."""
    cut(o, part, solver="EXACT", op="UNION")


def prism(name, poly, z0, z1, inset, m):
    """2D 다각형(CCW)을 z0→z1 로 세운 각기둥. 윗면을 inset 만큼 중심 쪽으로 줄여 챔퍼(사면) 형태."""
    n = len(poly)
    cx, cy = sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n
    def shrink(p):
        vx, vy = p[0] - cx, p[1] - cy
        d = math.hypot(vx, vy)
        k = max(d - inset, 0) / d
        return (cx + vx * k, cy + vy * k)
    verts = [(x, y, z0) for x, y in poly] + [(*shrink(p), z1) for p in poly]
    faces = [tuple(range(n))[::-1], tuple(range(n, 2 * n))] + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
    o = mesh_from_faces(name, verts, faces, m)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(o.data)
    bm.free()
    return o


def smooth(o, angle=35):
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(angle))


def join(objs, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    o = bpy.context.object
    o.name = name
    return o



BOLD_FONT = "C:/Windows/Fonts/arialbd.ttf"  # 굵은 글자용 (offset 으로 굵히면 외곽선이 겹쳐 불리언이 깨짐)


def _text_mesh(body, size, loc, rot, extrude, m, bold=False):
    bpy.ops.object.text_add(location=loc, rotation=rot)
    t = bpy.context.object
    t.data.body, t.data.size, t.data.extrude = body, size, extrude
    if bold and os.path.exists(BOLD_FONT):
        t.data.font = bpy.data.fonts.get("ArialBold") or bpy.data.fonts.load(BOLD_FONT)
        t.data.font.name = "ArialBold"
    t.data.align_x = t.data.align_y = "CENTER"
    bpy.ops.object.convert(target="MESH")
    t = bpy.context.object
    t.data.materials.append(m)
    return t


def engrave(o, body, size, loc, rot=(0, 0, 0), depth=0.005, inlay=None, normal=(0, 0, 1), bold=False, recess=0.00025):
    """텍스트를 표면에 음각(FLOAT 차집합). inlay 재질을 주면 파낸 홈에 그 재질의 글자를 0.0005 만큼 낮게 채워 넣는다(상감).
    커터 재질 = 본체 재질(TRANSFER 대비). normal 은 표면 바깥 방향(상감 위치 계산용)."""
    cut(o, _text_mesh(body, size, loc, rot, depth, o.data.materials[0], bold), solver="FLOAT")
    if inlay:  # 상감 윗면을 표면보다 recess 만큼 낮게 → 홈 벽이 보여 음각 느낌
        n = Vector(normal)
        half = (depth - recess) / 2
        cut(o, _text_mesh(body, size, Vector(loc) - n * (recess + half), rot, half, inlay, bold), solver="EXACT", op="UNION")


def mesh_from_faces(name, verts, faces, m):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    o = bpy.data.objects.new(name, me)
    o.data.materials.append(m)
    return link(o)


# ---------------------------------------------------------------- 쉬라우드
def build_shroud():
    """SUPRIM 윗면 구성(평평한 패널 분할). 모든 부속을 순차 UNION 으로 한 메시에 녹여 겹치는 면이 없다.
    윗면: 어두운 판 + 브러시드 실버 사선 밴드(+0.006) + 팬마다 각진 8각 프레임(+0.014) + 사선 경계 액센트 슬릿
    옆면: 스커트, +X 끝 캡(G-shared 상감) — 변경 없음."""
    zt = Z_SHROUD + 0.0375  # 윗판 윗면 0.2225
    dark, dk2 = M["shroud"](), M["shroud2"]()
    o = box("Shroud", (L, H, 0.075), (0, 0, Z_SHROUD), dark, bevel=0.045, segs=1)  # 둥근 베벨 대신 각진 챔퍼
    hy = H / 2 - 0.035  # 패널 y 한계(윗판 베벨 안쪽)
    # 사선 밴드: 아래변 x -1.0..0.45, 윗변 x -0.62..0.85 (오른쪽으로 0.38 기울어진 평행사변형)
    band = [(-1.0, -hy), (0.45, -hy), (0.85, hy), (-0.62, hy)]
    fuse(o, prism("_band", band, zt - 0.01, zt + 0.006, 0.002, M["brushed"]()))
    # 액센트 슬릿 2개: 밴드 왼쪽/오른쪽 경계선 바깥에 나란히
    def x_on(y, x0, x1):  # 경계선 위의 x (y=-hy→x0, y=hy→x1)
        return x0 + (y + hy) / (2 * hy) * (x1 - x0)
    for (x0, x1, side, ya, yb) in ((-1.0, -0.62, -1, -0.5, 0.05), (0.45, 0.85, 1, -0.25, 0.3)):
        near, far = 0.012 * side, 0.036 * side
        poly = [(x_on(ya, x0, x1) + near, ya), (x_on(ya, x0, x1) + far, ya), (x_on(yb, x0, x1) + far, yb), (x_on(yb, x0, x1) + near, yb)]
        if side < 0:
            poly = poly[::-1]
        fuse(o, prism("_slit", poly, zt - 0.01, zt + 0.008, 0.0, M["green"]()))
    # 팬 프레임: 각진 8각형 판(평평한 윗면 모서리 → 22.5° 회전), 서로 닿는 부분은 union 으로 합쳐짐
    for x in FAN_X:
        R = FAN_R + 0.12
        octa = [(x + R * math.cos(math.radians(22.5 + 45 * i)), R * math.sin(math.radians(22.5 + 45 * i))) for i in range(8)]
        fr = prism("_fr", octa, zt - 0.01, zt + 0.014, 0.006, dk2)
        fuse(o, fr)
    # 팬 우물: 원뿔로 깎아 경사 벽(위 r+0.075 → 아래 r+0.02), 프레임 윗면(zt+0.014)에서 판 아래(zt-0.11)까지
    zb, ztop = zt - 0.11, zt + 0.03
    for x in FAN_X:
        cut(o, cone("_cc", FAN_R + 0.02, FAN_R + 0.075, ztop - zb, (x, 0, (zb + ztop) / 2), dark))
    # 실버 원뿔 림: 경사 벽에 두께 0.012 실버 껍질을 씌움(바깥은 벽에 0.003 물림), 윗면은 프레임보다 0.001 낮게
    zr = zt + 0.013
    for x in FAN_X:
        h = zr - zb
        k = (FAN_R + 0.075 - FAN_R - 0.02) / (ztop - zb)  # 벽 경사(반지름/높이)
        r_out_top = FAN_R + 0.02 + k * h + 0.003
        shell = cone("_sh", FAN_R + 0.023, r_out_top, h, (x, 0, (zb + zr) / 2), M["silver"]())
        cut(shell, cone("_shi", FAN_R + 0.008, r_out_top - 0.015, h + 0.02, (x, 0, (zb + zr) / 2), M["silver"]()))
        fuse(o, shell)
    # ---- 옆면(변경 없음) ----
    for sy in (-1, 1):
        y = sy * (H / 2 - 0.02)
        for xa, xb in ((X0, X0 + 0.7), (X0 + 0.85, X0 + 1.55), (X0 + 1.7, X0 + 2.4), (X0 + 2.55, X1)):
            fuse(o, box("_s", (xb - xa, 0.04, SKIRT_H), ((xa + xb) / 2, y, SKIRT_ZC), dk2, bevel=0.012))
    # FE 식 실버 사이드 스트립: 윗단(x -0.55..X1, z 0.20) → 사선 → 아랫단(X0..-0.95, z 0.06). 스커트에 0.016 물리고 0.016 돌출
    z_hi, z_lo, x_a, x_b = 0.20, 0.06, -0.55, -0.95
    LIP_T, LIP_H = 0.032, 0.09  # 두께(절반 돌출), 높이
    for sy in (-1, 1):
        y = sy * (H / 2)
        fuse(o, box("_lip", (X1 - x_a, LIP_T, LIP_H), ((X1 + x_a) / 2, y, z_hi), M["silver"](), bevel=0.006, segs=1))
        fuse(o, box("_lip", (x_b - X0, LIP_T, LIP_H), ((X0 + x_b) / 2, y, z_lo), M["silver"](), bevel=0.006, segs=1))
        dx, dz = x_a - x_b, z_hi - z_lo
        diag = box("_lip", (math.hypot(dx, dz) + 0.05, LIP_T, LIP_H), (0, 0, 0), M["silver"](), bevel=0.006, segs=1)
        diag.rotation_euler.y = -math.atan2(dz, dx)
        apply_transform(diag)
        diag.location = ((x_a + x_b) / 2, y, (z_hi + z_lo) / 2)
        fuse(o, diag)
    cap = box("_e", (0.05, H, SKIRT_H), (X1 - 0.025, 0, SKIRT_ZC), dk2, bevel=0.012)
    # 끝 캡 면(+X) 중앙에 G 로고 상감 음각 (스피드 라인이 왼쪽으로 뻗으므로 중심을 살짝 오른쓱으로)
    engrave_logo_g(cap, ((X1, 0.05, SKIRT_ZC), (0, 1, 0), (0, 0, 1), (1, 0, 0)), R=0.17)
    fuse(o, cap)
    smooth(o, 40)
    tag(o, "Shroud", explode=(0, 0, 1.0), order=8, label="Shroud")
    print(f"[gpu] shroud polys={len(o.data.polygons)} mats={[m.name for m in o.data.materials]}")
    return o


# ---------------------------------------------------------------- 팬
def blade_mesh(r0, r1, ang_w, sweep, pitch, nr=9, na=6):
    verts, faces = [], []
    for i in range(nr + 1):
        t = i / nr
        r = r0 + (r1 - r0) * t
        w = ang_w * (1.0 - 0.35 * t)  # 바깥으로 갈수록 좁게
        base = sweep * t * t  # 뒤로 휘는 스윕
        for j in range(na + 1):
            a = base + (j / na - 0.5) * w
            z = pitch * (j / na - 0.5) * (0.55 + 0.45 * t)  # 피치 비틀림
            verts.append((r * math.cos(a), r * math.sin(a), z))
    for i in range(nr):
        for j in range(na):
            k = i * (na + 1) + j
            faces.append((k, k + 1, k + na + 2, k + na + 1))
    return verts, faces


def build_fan(idx, x, label):
    parts = []
    hub = cyl("_h", 0.15, 0.07, (x, 0, Z_FAN), M["hub"](), verts=64)
    add_bevel(hub, 0.012)
    parts.append(hub)
    parts.append(torus("_hr", 0.135, 0.007, (x, 0, Z_FAN + 0.035), M["silver"]()))
    for b in range(11):
        v, f = blade_mesh(0.13, FAN_R - 0.005, 0.44, 0.75, 0.085)
        o = mesh_from_faces("_b", v, f, M["blade"]())
        o.rotation_euler.z = 2 * math.pi * b / 11
        o.location = (x, 0, Z_FAN + 0.005)
        apply_transform(o)
        o.location = (x, 0, Z_FAN + 0.005)
        s = o.modifiers.new("S", "SOLIDIFY")
        s.thickness, s.offset = 0.009, 0
        apply_mods(o)
        parts.append(o)
    fan = join(parts, f"Fan_{idx}")
    engrave(fan, "nocase", 0.045, (x, 0, Z_FAN + 0.035), depth=0.003)  # 허브 캡 윗면 음각
    # 원점을 팬 중심에 두어 spin이 자기 축으로 돈다
    bpy.context.scene.cursor.location = (x, 0, Z_FAN)
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    smooth(fan, 45)
    dz = 1.5 + 0.1 * idx
    tag(fan, f"Fan_{idx}", explode=(0.12 * x, 0, dz), order=10 - idx, label=label, spin=1)
    return fan


# ---------------------------------------------------------------- 히트싱크
def build_fins():
    """핀 한 장 = 오브젝트 하나. -X→+X 순차(order 소수)로 떠오르고 높이는 파도(sin)."""
    z0, z1 = Z_FIN
    blocks = ((X0 + 0.16, -0.62), (-0.55, 0.55), (0.62, X1 - 0.12))
    out = []
    pitch = 0.042
    k = 0
    for i, (xa, xb) in enumerate(blocks):
        dx = (-0.35, 0.0, 0.35)[i]
        xs = []
        x = xa
        while x < xb:
            xs.append(x)
            x += pitch
        mid = len(xs) // 2
        for j, x in enumerate(xs):
            o = box("_f", (0.006, H - 0.30, z1 - z0), (x, -0.05, (z0 + z1) / 2), M["fin"]())  # y -0.54..0.44: 16핀(0.46~) 회피
            tag(o, f"Fin_{k:02d}",
                explode=(dx, 0, 0.62 + 0.15 * math.sin(k * 0.5)),  # 파도형 높이
                order=6.4 - k * 0.012,  # 6.4 → ~5.65: 히트파이프(5)와 쉬라우드(8) 사이, 앞 장부터 순차
                label="Fin stack" if (i == 1 and j == mid) else "")
            out.append(o)
            k += 1
    print(f"[gpu] fins={k}")
    return out


def build_heatpipes():
    out = []
    ys = (-0.42, -0.21, 0.0, 0.21, 0.42)
    for i, y in enumerate(ys):
        cu = bpy.data.curves.new("_hp", "CURVE")
        cu.dimensions, cu.bevel_depth, cu.bevel_resolution = "3D", 0.028, 6
        sp = cu.splines.new("BEZIER")
        pts = [(-1.25, y, -0.02), (-0.85, y, -0.04), (-0.55, y, -0.105), (0.1, y, -0.105), (0.4, y, -0.035), (1.4, y, -0.02)]
        sp.bezier_points.add(len(pts) - 1)
        for bp, p in zip(sp.bezier_points, pts):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
        o = link(bpy.data.objects.new("_hp", cu))
        o.data.materials.append(M["copper"]())
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
        bpy.ops.object.convert(target="MESH")
        o = bpy.context.object
        smooth(o, 60)
        tag(o, f"Heatpipe_{i}", explode=(0, 0.28 * y, 0.42 + 0.04 * i), order=5, label="Heatpipes" if i == 2 else "")
        out.append(o)
    return out


def build_coldplate():
    # x -0.72..0.36 (VRM 열 0.395 앞), y ±0.44 (16핀 0.46 아래) — 초크/캡/전원 커넥터와 겹치지 않음
    o = box("ColdPlate", (1.08, 0.88, 0.03), (-0.18, 0, Z_PLATE), M["nickel"](), bevel=0.008,
            explode=(0, 0, 0.32), order=4, label="Vapor chamber")
    # 좌우 VRM 브릿지: 초크/캡 위(Z_BRIDGE)에 얹힘. L: x -1.37..-1.03, R: x 0.39..1.05, y ±0.45
    box("PlateBridge_L", (0.34, 0.7, 0.02), (-1.2, 0.11, Z_BRIDGE), M["nickel"](), bevel=0.006, explode=(-0.1, 0, 0.32), order=4)
    box("PlateBridge_R", (0.66, 0.9, 0.02), (0.72, 0.0, Z_BRIDGE), M["nickel"](), bevel=0.006, explode=(0.1, 0, 0.32), order=4)
    return o


# ---------------------------------------------------------------- PCB
def build_pcb():
    pw = (X0 + 0.05, 1.05)
    pcb = box("PCB", (pw[1] - pw[0], H - 0.06, 0.016), ((pw[0] + pw[1]) / 2, -0.03, Z_PCB), M["pcb"](), order=0, label="PCB")
    # PCIe 커넥터(골드 핑거) — 짧은 쪽 11핀 + 노치 + 긴 쪽. 별도 오브젝트 3개, 서로 겹치는 부피 없음:
    #   Base 는 PCB 아래 모서리(y=-0.64)에 면으로만 접하고, 골드 핑거 자리는 Base 에서 파낸 홈에 끼움
    yb = -0.03 - (H - 0.06) / 2 - 0.055  # Base 중심: 위 모서리(-0.64)가 PCB 아래 모서리와 일치
    base = box("PCIe_Base", (0.93, 0.11, 0.016), (-0.72, yb, Z_PCB), M["pcb"](), explode=(0, -0.55, 0), order=0, label="PCIe x16")
    fingers = (("PCIe_Short", 0.12, -1.115), ("PCIe_Long", 0.74, -0.66))
    for name, w, x in fingers:
        cut(base, box("_g", (w, 0.09, 0.1), (x, yb - 0.01, Z_PCB), M["pcb"]()))
    for name, w, x in fingers:
        box(name, (w, 0.09, 0.02), (x, yb - 0.01, Z_PCB), M["gold"](), explode=(0, -0.55, 0), order=0)
    # GPU 다이 + 기판
    gx = -0.25
    box("Substrate", (0.5, 0.5, 0.01), (gx, 0.02, Z_PCB + 0.013), M["pcb"](), explode=(0, 0, 0.12), order=1)
    box("GPU_Die", (0.3, 0.3, 0.02), (gx, 0.02, Z_PCB + 0.028), M["die"](), bevel=0.003, explode=(0, 0, 0.14), order=2, label="GPU die")
    # VRAM 8개
    k = 0
    for i, (dx, dy) in enumerate(((-0.4, 0.22), (-0.4, -0.18), (0.4, 0.22), (0.4, -0.18), (-0.15, 0.4), (0.15, 0.4), (-0.15, -0.36), (0.15, -0.36))):
        box(f"VRAM_{i}", (0.15, 0.12, 0.012), (gx + dx, 0.02 + dy, Z_PCB + 0.014), M["chip"](), bevel=0.002,
            explode=(dx * 0.35, dy * 0.35, 0.16 + 0.015 * i), order=2, label="GDDR memory" if i == 2 else "")
    # VRM: 초크 + 모스펫 + 커패시터 (오른쪽/왼쪽 열)
    n = 0
    for col, xs in enumerate(((0.45, 0.62, 0.79, 0.96), (-1.15, -1.3))):
        for xi, x in enumerate(xs):
            for yi, y in enumerate((-0.35, -0.12, 0.11, 0.34)):
                if col == 1 and yi == 0:
                    continue
                if xi % 2 == 0:
                    box(f"Choke_{n}", (0.11, 0.11, 0.09), (x, y, Z_PCB + 0.053), M["chip"](), bevel=0.006,
                        explode=(0.08 * (1 if x > 0 else -1), 0, 0.2 + 0.01 * n), order=3, label="VRM chokes" if n == 1 else "")
                else:
                    c = cyl(f"Cap_{n}", 0.035, 0.085, (x, y, Z_PCB + 0.05), M["cap"](), verts=24,
                            explode=(0.08 * (1 if x > 0 else -1), 0, 0.22 + 0.01 * n), order=3, label="Capacitors" if n == 5 else "")
                    smooth(c, 50)
                    # 모스펫은 캡과 이웃 초크 사이 빈 틈(col0: x-0.085, col1: x+0.075)에
                    box(f"Mosfet_{n}", (0.04, 0.06, 0.012), (x - 0.085 if col == 0 else x + 0.07, y, Z_PCB + 0.014), M["chip"](),
                        explode=(0, 0, 0.13), order=1)
                n += 1
    # 16핀 전원 커넥터(상단 엣지)
    box("Power_16pin", (0.24, 0.12, 0.13), (0.55, H / 2 - 0.12, Z_PCB + 0.073), M["plastic"](), bevel=0.008,
        explode=(0, 0.45, 0.25), order=3, label="16-pin power")
    for i in range(6):
        for j in range(2):
            box("_pin", (0.012, 0.012, 0.06), (0.55 - 0.075 + 0.03 * i, H / 2 - 0.12 - 0.02 + 0.04 * j, Z_PCB + 0.11), M["gold"]())
    pins = join([o for o in bpy.data.objects if o.name.startswith("_pin")], "Power_Pins")
    tag(pins, "Power_Pins", explode=(0, 0.45, 0.25), order=3)
    # SMD 잔부품(작은 저항열) 몇 줄 — 하나로 join
    smds = []
    for i in range(26):
        smds.append(box("_smd", (0.03, 0.015, 0.008), (-1.2 + 0.09 * (i % 13), 0.56 if i < 13 else -0.61, Z_PCB + 0.012), M["chip"]()))  # 기판 가장자리 쪽: VRAM 저항열(±0.505/-0.445)·PCIe 띠와 안 겹치게
    s = join(smds, "SMD")
    tag(s, "SMD", explode=(0, 0, 0.1), order=1)

    # ---- 고도화: 배선 + 잔부품 (실제 GPU 기판 느낌)
    zt = Z_PCB + 0.008 + 0.0015  # PCB 윗면(Z_PCB+0.008) 위 얇은 배선층
    tr, vias = [], []

    def seg(x0, y0, x1, y1, w=0.005):
        """임의 각도 배선 한 토막(45° 꺾임용)."""
        L = math.hypot(x1 - x0, y1 - y0)
        o = box("_tr", (L + w, w, 0.003), ((x0 + x1) / 2, (y0 + y1) / 2, zt), M["trace"]())
        o.rotation_euler.z = math.atan2(y1 - y0, x1 - x0)
        apply_transform(o)
        tr.append(o)

    def route(pts, w=0.005):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg(x0, y0, x1, y1, w)

    def dog(x0, y0, x1, y1, w=0.005, first="x"):
        """맨해튼+45° 도그레그: 주축으로 곧게 가다 45° 로 꺾어 도착(first='x' 는 x 축부터)."""
        dx, dy = x1 - x0, y1 - y0
        if first == "x":
            if abs(dx) >= abs(dy):
                route([(x0, y0), (x1 - math.copysign(abs(dy), dx), y0), (x1, y1)], w)
            else:
                route([(x0, y0), (x1, y0 + math.copysign(abs(dx), dy)), (x1, y1)], w)
        else:
            if abs(dy) >= abs(dx):
                route([(x0, y0), (x0, y1 - math.copysign(abs(dx), dy)), (x1, y1)], w)
            else:
                route([(x0, y0), (x0 + math.copysign(abs(dy), dx), y1), (x1, y1)], w)

    def via(x, y, r=0.006):
        vias.append(cyl("_via", r, 0.0025, (x, y, zt + 0.0005), M["via"](), verts=10))

    def meander_x(x0, x1, y, w=0.005, amp=0.014, n=4):
        """가로 구간에 길이 맞춤 서펜타인(지그재그) n개."""
        pts = [(x0, y)]
        step = (x1 - x0) / (n * 2 + 1)
        x = x0 + step
        for i in range(n):
            pts += [(x, y), (x + step * 0.25, y + amp), (x + step * 0.75, y + amp), (x + step, y)]
            x += step * 2
        pts.append((x1, y))
        route(pts, w)

    # 배선 레이아웃(같은 층에서 교차 없음 — 아래 구역 배정을 지킨다)
    #   기판(Substrate) 가장자리 ±SUB 가 모든 다이 배선의 출발선(그 안쪽은 기판 아래 숨음)
    #   좌/우변: y∈[0.08,0.24] 위 VRAM 버스 12가닥 / y∈[-0.06,0.06] 전원(우: 4가닥, 좌: 디스플레이 8가닥) / y∈[-0.24,-0.08] 아래 VRAM 버스 12가닥
    #   상변: 위 VRAM 2개 버스 각 12가닥(x∈±[0.0,0.19]) / 하변: 아래 VRAM 2개 버스 + 가운데 틈(x∈±0.07)으로 PCIe 24가닥
    #   PCIe 는 틈에서 내려와 y -0.30~-0.44 에서 90°(45° 챔퍼) 좌회전해 커넥터 핑거로 내려감(동심 회전 → 교차 없음)
    #   좌 VRM 전원은 위 VRAM 위 빈 띠(y=0.345)로 돌아 기판 좌상단 모서리로, 디커플링 캡은 기판 네 모서리 바깥 클러스터
    SUB = 0.25
    def bus_side(sx, dy, n=12):
        """좌/우변 → VRAM(gx+sx*0.4, 0.02+dy). 45° 도그레그로 모여 들어감, 4가닥마다 서펜타인."""
        y0 = 0.02 + (0.16 if dy > 0 else -0.16)  # 띠 중심 ±0.16 → [0.08,0.24]/[−0.24,−0.08]
        xs, xe = gx + sx * SUB, gx + sx * (0.4 - 0.075)  # VRAM 안쪽 변
        for j in range(n):
            ys = y0 + (j - (n - 1) / 2) * 0.0145
            ye = 0.02 + dy + (j - (n - 1) / 2) * 0.0085
            if j % 4 == 1:
                xm = xs + sx * 0.02
                seg(xs, ys, xm, ys)
                x2 = xm + sx * 0.05
                (meander_x(xm, x2, ys, amp=0.005, n=3) if sx > 0 else meander_x(x2, xm, ys, amp=0.005, n=3))
                dog(x2, ys, xe, ye, first="x")
            else:
                dog(xs, ys, xe, ye, first="x")
            via(xe + sx * 0.01, ye, 0.0035)
    def bus_topbot(sy, dx, n=12):
        """상/하변 → VRAM(gx+dx, 0.02+sy*0.38…). 45° 도그레그."""
        x0 = gx + dx * 0.62  # 띠 중심 ±0.093 → 좌우 두 버스가 가운데서 안 겹침
        ys, ye = 0.02 + sy * SUB, 0.02 + (0.4 if sy > 0 else -0.36) - sy * 0.06
        for j in range(n):
            xs = x0 + (j - (n - 1) / 2) * 0.0155
            xe = gx + dx + (j - (n - 1) / 2) * 0.0105
            dog(xs, ys, xe, ye, first="y")
            via(xe, ye + sy * 0.01, 0.0035)
    # 1) 메모리 버스 8개
    bus_side(-1, 0.22); bus_side(-1, -0.18); bus_side(1, 0.22); bus_side(1, -0.18)
    bus_topbot(1, -0.15); bus_topbot(1, 0.15); bus_topbot(-1, -0.15); bus_topbot(-1, 0.15)

    # 2) 전원(+X VRM → 기판 우변 가운데 띠): 4가닥 동심 ㄴ자(안쪽부터 x 0.36/0.39/0.42/0.45 에서 꺾임 → 교차 없음)
    rows = (-0.35, -0.12, 0.11, 0.34)
    for k, yy in enumerate(rows):
        yd = -0.048 + k * 0.032  # 기판 우변 도착 높이(아래 행 → 아래)
        xb = 0.36 + k * 0.03 if yy < 0 else 0.45 - (k - 2) * 0.03  # 아래 두 행은 안쪽, 위 두 행은 바깥쪽에서 꺾어 동심
        route([(gx + SUB, yd), (xb, yd), (xb, yy), (0.99, yy)], w=0.02)
        for q in range(5):
            via(0.50 + q * 0.09, yy + 0.007 * (1 if q % 2 else -1), 0.0035)
    # 좌 VRM → 기판 좌상단: 위 VRAM(y≤0.30)과 그 저항열(0.325) 위, 좌상 VRAM(y≥0.36) 아래 빈 띠 y=0.345 로 돌아 들어감
    route([(-1.05, 0.11), (-0.98, 0.11), (-0.98, 0.345), (gx - 0.30, 0.345), (gx - 0.30, 0.20), (gx - SUB, 0.20)], w=0.022)
    for q in range(4):
        via(-0.9 + q * 0.1, 0.345 + 0.006 * (1 if q % 2 else -1), 0.0035)

    # 3) PCIe 24가닥: 하변 가운데 틈(x∈±0.07)에서 하단 VRAM 두 개 사이로 내려와, VRAM·저항열(y≥-0.45) 아래
    #    y -0.46…-0.54 띠에서 좌회전(45° 챔퍼, 동심) → 커넥터 핑거(y -0.585). 하단 SMD 열은 그 아래 y=-0.61
    N_PCIE, c = 24, 0.008
    for j in range(N_PCIE):
        xs = gx - 0.069 + j * 0.006
        xe = -1.06 + j * 0.029  # 핑거 위 도착 x (-1.06 … -0.39)
        yj = -0.46 - j * 0.0035  # 왼쪽 가닥이 먼저(위에서) 꺾인다 → 동심 회전
        route([(xs, 0.02 - SUB), (xs, yj + c), (xs - c, yj), (xe + c, yj), (xe, yj - c), (xe, -0.585)], w=0.0032)
        via(xe, -0.578, 0.0028)

    # 4) 디스플레이 8가닥: 기판 좌변 가운데 띠(y∈[-0.04,0.05]) → 좌 VRAM 두 개 사이 틈을 지나 브라켓 앞(x=-1.02)
    for j in range(8):
        yy = 0.005 + (j - 3.5) * 0.0125
        seg(gx - SUB, yy, -1.02, yy, w=0.0045)
        via(-1.03, yy, 0.0035)

    # 5) 제어 신호: PWM IC(초크 열 아래) → 초크 4열 밑변, BIOS IC(좌상단) → 기판 좌상단 모서리 3가닥
    for k, xk in enumerate((0.45, 0.62, 0.79, 0.96)):
        dog(0.69 + k * 0.012, -0.475, xk, -0.415, first="y", w=0.004)
    for k in range(3):
        route([(-0.92, 0.395 + k * 0.008), (-0.56 - k * 0.008, 0.395 + k * 0.008), (gx - SUB + 0.03 - k * 0.008, 0.02 + SUB + 0.03)], w=0.004)

    t = join(tr, "Traces")
    tag(t, "Traces", explode=(0, 0, 0.0), order=0)
    v = join(vias, "Vias")
    tag(v, "Vias", explode=(0, 0, 0.0), order=0)

    # 기판 우측 두 모서리 바깥(대각 방향) 디커플링 캡 클러스터(3×3). 좌측 모서리는 좌 VRM 전원선·BIOS 신호가 지나가므로 비움
    dec = []
    for cy_ in (-1, 1):
        for i in range(3):
            for j in range(3):
                dec.append(box("_dc", (0.016, 0.009, 0.007), (gx + 0.30 + i * 0.022, 0.02 + cy_ * (0.30 + j * 0.02), Z_PCB + 0.0115), M["chip"]()))
    d = join(dec, "SMD_Die")
    tag(d, "SMD_Die", explode=(0, 0, 0.11), order=1)
    # VRAM 바깥 변에 저항열 4개
    res = []
    for dx, dy in ((-0.4, 0.22), (-0.4, -0.18), (0.4, 0.22), (0.4, -0.18), (-0.15, 0.4), (0.15, 0.4), (-0.15, -0.36), (0.15, -0.36)):
        for j in range(4):
            res.append(box("_rs", (0.014, 0.008, 0.007), (gx + dx - 0.045 + j * 0.03, 0.02 + dy + (0.085 if dy > 0 else -0.085), Z_PCB + 0.0115), M["chip"]()))
    r = join(res, "SMD_VRAM")
    tag(r, "SMD_VRAM", explode=(0, 0, 0.105), order=1)
    box("PWM_IC", (0.08, 0.06, 0.012), (0.72, -0.52, Z_PCB + 0.014), M["ic"](), bevel=0.002, explode=(0, 0, 0.12), order=1)
    box("BIOS_IC", (0.06, 0.045, 0.01), (-0.95, 0.40, Z_PCB + 0.013), M["ic"](), bevel=0.002, explode=(0, 0, 0.12), order=1)

    # ---- 부속 추가(전부 배선 구역과 겹치지 않는 빈 자리에)
    # 입력 필터 캡 3개: 전원 커넥터 옆, 우 VRM 윗행(≤0.395) 위 — 큰 전해캡
    for i in range(3):
        c = cyl(f"Cap_F{i}", 0.036, 0.07, (0.80 + 0.09 * i, 0.46, Z_PCB + 0.043), M["cap"](), verts=24, explode=(0.06, 0.2, 0.24), order=3)  # 높이 0.07: 윗면 -0.122 < PlateBridge_R 밑면 -0.095
        smooth(c, 50)
    # 출력 필터 캡 3개: 우 VRM 아랫행(-0.35 행, 초크 밑변 -0.405) 아래, PWM IC(0.68~) 왼쪽 빈 띠
    for i in range(3):
        c = cyl(f"Cap_O{i}", 0.03, 0.06, (0.30 + 0.10 * i, -0.50, Z_PCB + 0.038), M["cap"](), verts=24, explode=(0, -0.15, 0.2), order=3)
        smooth(c, 50)
    # 팬 헤더 2개(4핀): 상단 가장자리, 위 VRAM 저항열(x≤0.08) 오른쪽·전원 커넥터(x≥0.43) 왼쓸 사이
    for i in range(2):
        hx = 0.20 + 0.11 * i
        box(f"FanHeader_{i}", (0.08, 0.04, 0.05), (hx, 0.50, Z_PCB + 0.033), M["plastic"](), bevel=0.003, explode=(0, 0.2, 0.18), order=3)
        for k in range(4):
            box("_fp", (0.008, 0.008, 0.03), (hx - 0.024 + 0.016 * k, 0.50, Z_PCB + 0.07), M["gold"]())
    fp = join([o for o in bpy.data.objects if o.name.startswith("_fp")], "FanHeader_Pins")
    tag(fp, "FanHeader_Pins", explode=(0, 0.2, 0.18), order=3)
    # 디스플레이 컨트롤러 IC + 주변 캡 6개 + 크리스털: 좌하단 빈 블록(디스플레이 신호 y≥-0.04 아래, PCIe 띠 y≤-0.46 위, 좌하 VRAM x≥-0.725 왼쪽)
    box("DP_IC", (0.10, 0.08, 0.012), (-0.88, -0.26, Z_PCB + 0.014), M["ic"](), bevel=0.002, explode=(0, 0, 0.12), order=1)
    dpc = [box("_dpc", (0.016, 0.009, 0.007), (-0.95 + 0.03 * i, -0.36, Z_PCB + 0.0115), M["chip"]()) for i in range(6)]
    d2 = join(dpc, "SMD_DP")
    tag(d2, "SMD_DP", explode=(0, 0, 0.1), order=1)
    box("Crystal", (0.05, 0.03, 0.02), (-0.60, -0.36, Z_PCB + 0.018), M["silver"](), bevel=0.006, explode=(0, 0, 0.12), order=1)
    # 쿨러 마운팅 홀 4개: 기판 모서리 대각 바깥. PCB 를 관통하고 도금 링을 두름
    rings = []
    for hx, hy in ((gx - 0.42, 0.44), (gx - 0.42, -0.40), (gx + 0.42, 0.44), (gx + 0.42, -0.40)):
        ring = cyl("_ring", 0.034, 0.004, (hx, hy, Z_PCB + 0.009), M["via"](), verts=32)
        cut(ring, cyl("_h", 0.02, 0.05, (hx, hy, Z_PCB + 0.009), M["via"](), verts=32))
        rings.append(ring)
        cut(pcb, cyl("_h", 0.02, 0.1, (hx, hy, Z_PCB), M["pcb"](), verts=32))
    rg = join(rings, "Hole_Rings")
    tag(rg, "Hole_Rings", explode=(0, 0, 0.0), order=0)
    return pcb


# ---------------------------------------------------------------- 백플레이트/브라켓
def build_backplate():
    o = box("Backplate", (L - 0.08, H - 0.04, 0.02), (0.04, 0, Z_BACK), M["shroud"](), bevel=0.01)
    # 플로우스루 벤트(+X 끝) + 슬롯 3개
    cut(o, box("_v", (0.55, 0.9, 0.2), (1.2, 0.0, Z_BACK), M["shroud"]()))
    for i in range(3):
        cut(o, box("_v2", (0.4, 0.04, 0.2), (-0.1 + 0.0 * i, -0.45 + 0.08 * i, Z_BACK), M["shroud"]()))
    # 벤트 안 격자 바: 양 끝을 판에 0.02 씩 물려 UNION → 백플레이트와 한 덩어리
    for i in range(7):
        cut(o, box("_bb", (0.02, 0.94, 0.012), (1.2 - 0.24 + 0.08 * i, 0, Z_BACK), M["shroud2"]()), op="UNION")
    smooth(o, 40)
    tag(o, "Backplate", explode=(0, 0, -0.85), order=-1, label="Backplate")
    return o


def build_bracket():
    zlo, zhi = Z_BACK - 0.01, Z_SHROUD + 0.0375  # 백플레이트 바닥 ~ 쉬라우드 윗면
    zc = (zlo + zhi) / 2
    o = box("IO_Bracket", (0.02, H - 0.05, zhi - zlo), (X0 - 0.03, 0.0, zc), M["silver"](), bevel=0.004)
    for i, (y, w) in enumerate(((-0.42, 0.18), (-0.18, 0.18), (0.06, 0.18), (0.3, 0.16))):
        cut(o, box("_p", (0.1, w, 0.06 if i < 3 else 0.055), (X0 - 0.03, y, zc + 0.05), M["silver"]()))
    smooth(o, 40)
    tag(o, "IO_Bracket", explode=(-0.7, 0, 0), order=2, label="I/O bracket")
    # 상단 고정 탭
    box("Bracket_Tab", (0.02, 0.12, 0.14), (X0 - 0.03, H / 2 + 0.045, zhi - 0.07), M["silver"](), bevel=0.004, explode=(-0.7, 0, 0), order=2)
    # 포트 본체(브라켓 뒤 검은 박스)
    for i, y in enumerate((-0.42, -0.18, 0.06, 0.3)):
        box(f"Port_{i}", (0.16, 0.16, 0.05), (X0 + 0.07, y, zc + 0.05), M["plastic"](), explode=(0, 0, 0.05), order=1,
            label="DisplayPort / HDMI" if i == 1 else "")
    return o


# ---------------------------------------------------------------- 씬/렌더
def setup_scene():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = 24
    sc.render.resolution_x, sc.render.resolution_y = 1280, 800
    sc.render.film_transparent = False
    w = bpy.data.worlds.new("World")
    sc.world = w
    w.use_nodes = True
    bg = next(n for n in w.node_tree.nodes if n.bl_idname == "ShaderNodeBackground")  # 이름은 UI 언어 의존 → 타입으로
    bg.inputs[0].default_value = (0.02, 0.02, 0.022, 1)
    bg.inputs[1].default_value = 1.0
    # 라이트: 키/필/림
    for name, loc, energy, size in (("Key", (2.5, -3.5, 4.5), 900, 2.5), ("Fill", (-3.5, 2.0, 3.0), 300, 3.0), ("Rim", (0.5, 3.5, 2.0), 500, 1.5)):
        bpy.ops.object.light_add(type="AREA", location=loc)
        lt = bpy.context.object
        lt.name, lt.data.energy, lt.data.size = name, energy, size
        lt.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    bpy.ops.object.camera_add(location=(3.6, -4.6, 3.3))
    cam = bpy.context.object
    cam.name = "Camera"
    cam.data.lens = 45
    cam.rotation_euler = (Vector((0, 0.05, -0.05)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam


def parts():
    return [o for o in bpy.data.objects if o.type == "MESH" and "explode" in o]


def render(path, exploded):
    if exploded:
        for o in parts():
            o.location += Vector(o["explode"]) * 0.8
        bpy.context.scene.camera.location = (5.2, -6.4, 5.0)
        bpy.context.scene.camera.rotation_euler = (Vector((0, 0, 0.35)) - bpy.context.scene.camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    if exploded:
        for o in parts():
            o.location -= Vector(o["explode"]) * 0.8


# ---------------------------------------------------------------- main
def main():
    clear()
    build_shroud()
    for i, (x, lb) in enumerate(zip(FAN_X, ("Axial fan ×3", "", ""))):
        build_fan(i, x, lb)
    build_fins()
    build_heatpipes()
    build_coldplate()
    build_pcb()
    build_backplate()
    build_bracket()
    setup_scene()

    ps = parts()
    tris = sum(len(o.data.polygons) for o in ps)
    print(f"[gpu] parts={len(ps)} polys={tris}")
    for o in ps:
        if len(o.data.vertices) == 0:
            print("  !! EMPTY", o.name)

    if BLEND:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(BLEND))
        print("[gpu] saved", BLEND)
    if OUT:
        bpy.ops.object.select_all(action="DESELECT")
        for o in ps:
            o.select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=os.path.abspath(OUT), export_format="GLB", use_selection=True, export_extras=True, export_apply=True,
            export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
            export_yup=True, export_texcoords=False, export_animations=False, export_lights=False, export_cameras=False,
        )
        print("[gpu] exported", OUT)
    if RENDER:
        render(f"{RENDER}/assembled.png", False)
        render(f"{RENDER}/exploded.png", True)


if __name__ == "__main__":  # 헬퍼만 import 해서 쓸 수 있게
    main()
