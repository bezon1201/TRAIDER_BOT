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
1.65 DCA_ANCHOR_UI_TWEAK: после установки якоря скрываем FIX/MA30/PRICE, остаются BUDGET/LEVELS/ANCHOR/OFF.
1.66 DCA_ROLLOVER_ANCHOR: при ROLLOVER обновляем anchor_price в dca_config по свежему state (команда и обе кнопки ROLLOVER).
1.67 PRICE_TRACK_LAST_FIX: get_last_price_from_state читает last из trading_params.price.last.
1.68 CARD_MAIN_MENU: интеграция нового формата карточки MAIN MENU через card_text.build_symbol_card_text.
1.69 CARD1_MAIN_MENU: новый формат карточки MAIN MENU по шаблону CARD1.

3.70 CARD_PRICE_ROUND: Price/MA30 в карточке показываются целыми долларами.
3.71 CARD_ALIGN_TABS: CARD1 нижний блок через табы (Grid/Price/Anchor/Average/Budget).
3.72 CARD_MONOSPACE: главная карточка отправляется в <pre>-блоке (моноширинный шрифт) с выравниванием по колонкам.
3.73 CARD_PRE_MENU: команда /menu теперь тоже показывает карточку в <pre>-блоке (ParseMode.HTML).
3.74 CARD_ALIGN_SPACE: нижний блок Grid/Price/Anchor/Average/Budget выравнен пробелами в моноширинном <pre>-блоке.
