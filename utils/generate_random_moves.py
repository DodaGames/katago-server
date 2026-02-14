import random


def generate_random_moves(dimension: int, num_moves: int):
    # 바둑 좌표계: I 제외
    letters = "ABCDEFGHJKLMNOPQRST"
    coords = letters[:dimension]

    # 모든 좌표 생성
    all_positions = [f"{x}{y}" for x in coords for y in range(1, dimension + 1)]

    # 착수할 좌표 랜덤 선택 (중복 없음)
    selected_positions = random.sample(all_positions, num_moves)

    moves = []
    for i, pos in enumerate(selected_positions):
        color = "B" if i % 2 == 0 else "W"  # 흑/백 교대
        moves.append([color, pos])

    return moves


if __name__ == "__main__":
    print(generate_random_moves(19, 5))
