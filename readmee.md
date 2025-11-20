1.38 Карточка главного меню (CARD) и модуль card_text для вывода параметров DCA-кампании.
1.52 CONFIG: подменю CONFIG недоступно при активной DCA-кампании.
1.53 DCA_CONFIG: расширен формат dca_config.json (anchor_mode/offset) с обратной совместимостью.
1.54 DCA_ANCHOR: утилита compute_anchor_from_config (FIX/MA30/PRICE + offset).
1.55 MARKET_STATE: чтение last price из <SYMBOL>state.json через coin_state.get_last_price_from_state.
1.56 DCA_GRID: использование anchor_mode/offset и PRICE/MA30 при построении DCA-сетки.
1.57 DCA_ANCHOR_UI: каркас мини-подменю ANCHOR (FIX/MA30/PRICE) в DCA/CONFIG.
1.58 DCA_ANCHOR_UI: обработчики FIX/MA30/PRICE без изменения конфига (шаг 5.2).
1.59 DCA_ANCHOR_FIX: полный сценарий ввода FIX через подменю ANCHOR (шаг 5.3).
1.60 DCA_ANCHOR_MA30: выбор режима MA30 через подменю ANCHOR без ввода числа (шаг 5.4).
1.61 DCA_ANCHOR_PRICE: выбор режима PRICE через подменю ANCHOR без ввода числа (шаг 5.5).
1.62 DCA_ANCHOR_MA30_OFFSET: при нажатии MA30 показываем запрос offset и сохраняем режим MA30+offset.
1.63 DCA_ANCHOR_MA30_OFFSET_UI: после ввода offset остаёмся в подменю DCA/CONFIG для удобного нажатия ON.
1.64 DCA_ANCHOR_PRICE_OFFSET: режим PRICE со слежением и offset по аналогии с MA30.
