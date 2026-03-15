def run(input_data):

    assignment_text = input_data["assignment"]

    score = 0

    if len(assignment_text) > 500:
        score += 5

    if "character" in assignment_text:
        score += 5

    print("Assignment score:", score)
