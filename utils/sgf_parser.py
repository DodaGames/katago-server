"""SGF 좌표 <-> KataGo(GTP) 좌표 변환 및 간단한 SGF 파서.

이 프로젝트의 SGF는 핸디캡/분기 없이 순수 수순(;B[xx];W[xx]...)만 있는
단순한 형태이므로, 범용 SGF 라이브러리 없이 정규식으로 충분히 파싱 가능하다.
"""

import re

GTP_COLS = "ABCDEFGHJKLMNOPQRST"  # I는 건너뜀 (GTP/KataGo 관례)


def sgf_coord_to_gtp(sgf_coord: str, board_size: int) -> str:
    """SGF 좌표(예: 'cc')를 KataGo가 받는 GTP 좌표(예: 'C3')로 변환. 빈 문자열은 pass."""
    if not sgf_coord:
        return "pass"
    col_idx = ord(sgf_coord[0]) - ord("a")
    row_idx = ord(sgf_coord[1]) - ord("a")
    gtp_col = GTP_COLS[col_idx]
    gtp_row = board_size - row_idx
    return f"{gtp_col}{gtp_row}"


def parse_sgf_game(sgf_content: str):
    """sgfContent 문자열에서 SZ, KM, 수순 목록(KataGo 형식)을 추출.

    반환: {"board_size": int, "komi": float, "moves": [["B","Q16"], ...]}
    파싱 불가 시 None.
    """
    sz_m = re.search(r"SZ\[([^\]]+)\]", sgf_content)
    if not sz_m:
        return None
    sz_raw = sz_m.group(1)
    # SZ[7] 또는 드물게 SZ[7:7] 형태 대비
    board_size = int(sz_raw.split(":")[0])

    km_m = re.search(r"KM\[([^\]]*)\]", sgf_content)
    komi = float(km_m.group(1)) if km_m and km_m.group(1) else 0.0

    raw_moves = re.findall(r";([BW])\[([a-z]*)\]", sgf_content)
    moves = [
        [color, sgf_coord_to_gtp(coord, board_size)] for color, coord in raw_moves
    ]
    return {"board_size": board_size, "komi": komi, "moves": moves}
