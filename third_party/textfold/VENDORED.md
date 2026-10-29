# TextFold, вшит в Universal без изменений

- Источник: https://github.com/chenxiachan/textfold
- Версия: 0.10.40, коммит `5062eacc0b1e2a2528e0906414f5073033051a30`
- Лицензия: Apache License 2.0 (см. `LICENSE` и `NOTICE` рядом). Файл `NOTICE` перечисляет и вложенные работы
  (шрифты Inter и Source Serif 4, KaTeX, Mermaid, PDF.js): их лицензии остаются в силе.
- Что вшито: пакет `textfold/` целиком (Python и веб-часть читалки). Расширение для браузера, приложение
  на Electron, тесты и документация не вшиты.
- Изменений в коде нет. Universal запускает его сервер внутри себя (`reader.py`), данные кладёт в
  `<папка данных Universal>/textfold`.
- Обновить: заменить папку `textfold/` свежей из репозитория, поправить номер версии и коммит здесь,
  прогнать `tests/test_app.py -k reader`.
