def make_bold_rows(df, title, filter_col, cols_to_bold_dict):
    if title in cols_to_bold_dict:
        row_identifier = cols_to_bold_dict[title]
    else:
        return []
    conditional_styling = []

    for key in row_identifier:
        idx = df.loc[df[filter_col] == key].index.values[0]
        conditional_styling.append({"if": {"row_index": idx}, "fontWeight": "bold", })

    return conditional_styling
