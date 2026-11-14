from tagger.claude_tagger import MISC_OPTION, _result


def tag(answer, name="", options=("Milagro Blanco Tequila",), is_alcoholic=True):
    return _result(list(options), {"answer": answer, "brand_label_name": name, "is_alcoholic": is_alcoholic,
                                   "skip_reason": ""}, [])


def test_echoed_item_prefix_matches_option():
    r = tag("Milagro Silver Vap - Milagro Blanco Tequila")
    assert (r.tag, r.selected_option, r.error) == ("Milagro Blanco Tequila", "Milagro Blanco Tequila", None)


def test_echoed_item_prefix_with_own_name():
    r = tag("Evan Williams - Single Barrel 250th - Evan Williams Single Barrel Bourbon")
    assert (r.tag, r.selected_option, r.suggested_name) == ("Evan Williams Single Barrel Bourbon", None,
                                                            "Evan Williams Single Barrel Bourbon")


def test_own_name_in_answer():
    assert tag("Monaco Blue Crush Cocktail").tag == "Monaco Blue Crush Cocktail"
    assert tag("Monaco Blue Crush Cocktail", name="Monaco Blue Crush Vodka Cocktail").tag == \
        "Monaco Blue Crush Vodka Cocktail"


def test_misc_and_empty_unchanged():
    assert tag(MISC_OPTION, is_alcoholic=False).tag == MISC_OPTION
    assert tag("").error


def test_misc_category_kept_only_for_misc():
    def misc(answer, category, is_alcoholic=False):
        return _result(["Milagro Blanco Tequila"], {"answer": answer, "brand_label_name": "", "skip_reason": "",
                                                    "is_alcoholic": is_alcoholic, "misc_category": category}, []).misc_category
    assert misc(MISC_OPTION, " Chips ") == "Chips"
    assert misc(MISC_OPTION, "") is None and misc(MISC_OPTION, None) is None
    assert misc("Milagro Blanco Tequila", "Food", is_alcoholic=True) is None  # a stray category on a liquor is dropped


def test_misc_category_from_pass_2_review():
    review = {"verdict": "AGREE", "correct_answer": MISC_OPTION, "correct_brand_label_name": "",
              "correct_is_alcoholic": False, "correct_misc_category": "Non-Alcoholic Beverage", "explanation": "soda"}
    assert _result([], review, []).misc_category == "Non-Alcoholic Beverage"
