from telegram import KeyboardButton, ReplyKeyboardMarkup

from utils import nav_labels as N


def main_menu(is_whitelist: bool) -> ReplyKeyboardMarkup:
    base = [
        [KeyboardButton(N.BTN_INTERVIEWS), KeyboardButton(N.BTN_GUIDE)],
        [KeyboardButton(N.BTN_SITE)],
    ]
    if is_whitelist:
        base.append([KeyboardButton(N.BTN_INVITE)])
    return ReplyKeyboardMarkup(base, resize_keyboard=True)


def invite_flow_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(N.BTN_CANCEL_INVITE)],
            [KeyboardButton(N.BTN_GUIDE)],
        ],
        resize_keyboard=True,
    )


def interview_hub_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(N.BTN_READ_INTERVIEWS), KeyboardButton(N.BTN_SHARE_INTERVIEW)],
            [KeyboardButton(N.BTN_BACK_HOME)],
        ],
        resize_keyboard=True,
    )


def interview_tell_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(N.BTN_STORY_DONE)],
            [KeyboardButton(N.BTN_STORY_CANCEL)],
        ],
        resize_keyboard=True,
    )
