"""캡처(따냄)를 반영한 최소 바둑 보드 시뮬레이터.

이 프로젝트의 KataGo 쿼리는 착수 좌표만 다루고 보드 점유 상태를 돌려주지
않으므로, "빈 교차점 수"(유효성 게이트 계산용)는 직접 재생해서 구해야 한다.
"""

from utils.sgf_parser import GTP_COLS


def _gtp_to_xy(coord: str):
    """'C3' 같은 GTP 좌표를 (col, row) 정수 인덱스로 변환. 'pass'는 None."""
    if not coord or coord.lower() == "pass":
        return None
    col = GTP_COLS.index(coord[0])
    row = int(coord[1:])
    return (col, row)


def _neighbors(x, y, size):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 1 <= nx <= size and 1 <= ny <= size:
            yield (nx, ny)


def _group_liberties(board, start, color):
    """start가 속한 동일 색 그룹 전체와 그 그룹의 자유도(liberty) 집합을 반환."""
    stack = [start]
    group = set()
    liberties = set()
    while stack:
        p = stack.pop()
        if p in group:
            continue
        group.add(p)
        for n in _neighbors(p[0], p[1], board["size"]):
            occ = board["stones"].get(n)
            if occ is None:
                liberties.add(n)
            elif occ == color and n not in group:
                stack.append(n)
    return group, liberties


def compute_empty_counts_before_each_move(moves, board_size):
    """moves(예: [["B","C3"], ["W","D4"], ...])를 순서대로 재생하면서,
    각 수를 두기 '직전' 시점의 빈 교차점 수를 리스트로 반환한다.
    반환 리스트 길이 == len(moves), i-번째 값 == (i+1)번째 수 직전의 빈 교차점 수.
    """
    board = {"size": board_size, "stones": {}}
    total_points = board_size * board_size
    empty_counts_before = []

    for color, coord in moves:
        empty_counts_before.append(total_points - len(board["stones"]))

        xy = _gtp_to_xy(coord)
        if xy is None:  # pass
            continue

        opponent = "W" if color == "B" else "B"
        board["stones"][xy] = color

        # 상대 그룹 중 자유도가 0이 된 그룹을 제거 (따냄)
        captured = set()
        for n in _neighbors(xy[0], xy[1], board_size):
            if board["stones"].get(n) == opponent and n not in captured:
                group, liberties = _group_liberties(board, n, opponent)
                if not liberties:
                    captured.update(group)
        for p in captured:
            del board["stones"][p]

        # 자살수 방지 규칙(둔 돌 자신의 그룹이 자유도 0이면 제거) - 실제 대국 데이터에는
        # 발생하지 않을 것으로 예상되나 방어적으로 처리
        own_group, own_liberties = _group_liberties(board, xy, color)
        if not own_liberties:
            for p in own_group:
                del board["stones"][p]

    return empty_counts_before
