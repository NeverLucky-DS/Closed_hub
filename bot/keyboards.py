from telegram import KeyboardButton, ReplyKeyboardMarkup


def main_menu(is_whitelist: bool) -> ReplyKeyboardMarkup:
    if is_whitelist:
        rows = [[KeyboardButton("Добавить участника"), KeyboardButton("Справка")]]
    else:
        rows = [[KeyboardButton("Что отправить"), KeyboardButton("Справка")]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def invite_flow_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Отмена")],
            [KeyboardButton("Добавить участника"), KeyboardButton("Справка")],
        ],
        resize_keyboard=True,
    )
