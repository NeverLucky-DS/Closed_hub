"""Подписи кнопок и синонимы для старых клавиатур в кэше Telegram."""

# Главное меню
BTN_INTERVIEWS = "Собесы"
BTN_GUIDE = "Как пользоваться"
BTN_SITE = "Сайт"
BTN_INVITE = "Пригласить человека"

GUIDE_ALIASES = frozenset({BTN_GUIDE, "Справка", "Что отправить"})
INVITE_ALIASES = frozenset({BTN_INVITE, "Добавить участника"})

# Приглашение
BTN_CANCEL_INVITE = "Отменить"

# Сбор контекста HR (обрабатывается только если открыт черновик awaiting_context)
BTN_CANCEL_HR = "Отменить HR"
HR_CANCEL_ALIASES = frozenset(
    {
        "отменить",
        "отмена",
        BTN_CANCEL_HR.lower(),
        "отменить hr",
        "отмена hr",
        "отменить добавление hr",
        "не добавлять hr",
        "отменить добавление",
    }
)

# Раздел «Собесы»
BTN_READ_INTERVIEWS = "Читать по компаниям"
BTN_SHARE_INTERVIEW = "Рассказать про собес"
BTN_BACK_HOME = "В главное меню"

READ_ALIASES = frozenset({BTN_READ_INTERVIEWS, "Узнать"})
SHARE_ALIASES = frozenset({BTN_SHARE_INTERVIEW, "Рассказать"})
BACK_HOME_ALIASES = frozenset({BTN_BACK_HOME, "Назад"})

# Режим рассказа
BTN_STORY_DONE = "Готово, сохранить"
BTN_STORY_CANCEL = "Выйти без сохранения"

DONE_ALIASES = frozenset({BTN_STORY_DONE, "На этом всё"})
CANCEL_FLOW_ALIASES = frozenset({BTN_STORY_CANCEL, "Отмена"})

INVITE_CANCEL_ALIASES = frozenset({BTN_CANCEL_INVITE, "Отмена"})
