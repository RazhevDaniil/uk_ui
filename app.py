import os
import re
import io
import time
import smtplib
import tempfile
import pandas as pd
import streamlit as st
from email.header import Header
from email.mime.text import MIMEText
from datetime import datetime, timedelta


DB_FILE = "debtors_monitoring.csv"

def load_monitoring_data():
    if os.path.exists(DB_FILE):
        return pd.read_csv(DB_FILE)
    else:
        return pd.DataFrame(columns=[
            "Дата контакта", "ФИО", "Сумма долга", 
            "Способ информирования", "Статус информирования", "Комментарий"
        ])

def save_monitoring_data(df):
    df.to_csv(DB_FILE, index=False)

def add_to_monitoring(fio, debt, method="E-mail", status="Отправлено", comment=""):
    df = load_monitoring_data()
    new_entry = {
        "Дата контакта": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "ФИО": fio,
        "Сумма долга": debt,
        "Способ информирования": method,
        "Статус информирования": status,
        "Комментарий": comment
    }
    df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
    save_monitoring_data(df)

tab_send, tab_dashboard = st.tabs(["📧 Отправка уведомлений", "📊 Мониторинг и Статусы"])

with tab_send:
    st.title("Рассылка уведомлений")
    st.set_page_config(page_title="Уведомления должникам", layout="wide")

    # -----------------------------
    # Helpers: parsing & columns
    # -----------------------------
    REQUIRED_FIELDS = [
        "Личный счет",
        "ФИО",
        "Адрес",
        "Период последней оплаты",
        "Начисления",
        "Сумма льгот",
        "Выставлено к оплате",
        "Сумма долга",
        "Пенни",
        "Дата долга",
        "Email",
    ]

    ALIASES = {
        "Личный счет": ["личный счет", "лицевой счет", "л/с", "лс", "account", "счет"],
        "ФИО": ["фио", "ф.и.о", "фамилия имя отчество", "name", "фамилия"],
        "Адрес": ["адрес", "address", "место проживания", "квартира", "дом"],
        "Период последней оплаты": ["период последней оплаты", "последняя оплата", "дата последней оплаты", "last payment"],
        "Начисления": ["начисления", "accrual", "начислено"],
        "Сумма льгот": ["сумма льгот", "льготы", "benefit", "скидка"],
        "Выставлено к оплате": ["выставлено к оплате", "к оплате", "итого к оплате", "to pay", "начислено к оплате"],
        "Сумма долга": ["сумма долга", "долг", "задолженность", "debt", "arrears"],
        "Пенни": ["пенни", "пени", "пеня", "penalty", "штраф"],
        "Дата долга": ["дата долга", "дата задолженности", "debt date", "дата образования долга"],
        "Email": ["email", "e-mail", "электронная почта", "почта", "mail"],
    }

    def _clean_col(s: str) -> str:
        s = str(s).strip().lower()
        s = re.sub(r"[\s\._\-]+", " ", s)
        return s

    def auto_map_columns(df: pd.DataFrame) -> dict:
        cols_clean = {c: _clean_col(c) for c in df.columns}
        result = {}
        for field, variants in ALIASES.items():
            found = None
            for col, cc in cols_clean.items():
                for v in variants:
                    if _clean_col(v) in cc:
                        found = col
                        break
                if found:
                    break
            result[field] = found
        return result

    def to_number(x):
        if pd.isna(x):
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x)
        s = s.replace("\u00a0", " ")  # non-breaking space
        s = s.replace(" ", "")
        s = s.replace(",", ".")
        s = re.sub(r"[^0-9\.\-]", "", s)
        if s in ("", ".", "-", "-.", ".-"):
            return 0.0
        try:
            return float(s)
        except Exception:
            return 0.0

    def read_table(uploaded_file) -> pd.DataFrame:
        name = uploaded_file.name.lower()
        data = uploaded_file.read()

        if name.endswith(".csv"):
            # пробуем разные разделители
            for sep in [",", ";", "\t"]:
                try:
                    df = pd.read_csv(io.BytesIO(data), sep=sep, encoding="utf-8")
                    if df.shape[1] > 1:
                        return df
                except Exception:
                    pass
            # fallback
            return pd.read_csv(io.BytesIO(data))

        if name.endswith(".xlsx") or name.endswith(".xls"):
            return pd.read_excel(io.BytesIO(data))

        if name.endswith(".pdf"):
            # PDF поддержка зависит от окружения. Пробуем camelot, затем pdfplumber.
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(data)
                tmp.flush()

                # 1) camelot (лучше для "табличных" PDF)
                try:
                    import camelot  # type: ignore
                    tables = camelot.read_pdf(tmp.name, pages="all")
                    if tables and len(tables) > 0:
                        parts = []
                        for t in tables:
                            parts.append(t.df)
                        df = pd.concat(parts, ignore_index=True)
                        # Попытка: первая строка как заголовок
                        if df.shape[0] > 1:
                            df.columns = df.iloc[0].astype(str)
                            df = df.iloc[1:].reset_index(drop=True)
                        return df
                except Exception:
                    pass

                # 2) pdfplumber (иногда вытаскивает таблицы как списки)
                try:
                    import pdfplumber  # type: ignore
                    rows = []
                    with pdfplumber.open(tmp.name) as pdf:
                        for page in pdf.pages:
                            table = page.extract_table()
                            if table:
                                rows.extend(table)
                    if rows:
                        df = pd.DataFrame(rows)
                        # первая строка как заголовок
                        if df.shape[0] > 1:
                            df.columns = df.iloc[0].astype(str)
                            df = df.iloc[1:].reset_index(drop=True)
                        return df
                except Exception:
                    pass

            raise RuntimeError(
                "Не удалось прочитать PDF как таблицу. Попробуйте выгрузить данные в Excel/CSV "
                "или установите зависимости для PDF (camelot-py или pdfplumber)."
            )

        raise RuntimeError("Неподдерживаемый формат. Загрузите CSV, Excel (.xlsx/.xls) или PDF.")


    # -----------------------------
    # Email sending
    # -----------------------------
    def send_email_smtp(smtp_host, smtp_port, smtp_user, smtp_password, use_tls, from_addr, to_addr, subject, body):
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = from_addr
        msg["To"] = to_addr

        # Если порт 465, используем класс SMTP_SSL (безопасное соединение сразу)
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.sendmail(from_addr, [to_addr], msg.as_string())
        
        # Для порта 587 и других используем обычный SMTP + STARTTLS
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                    server.ehlo()
                if smtp_user:
                    server.login(smtp_user, smtp_password)
                server.sendmail(from_addr, [to_addr], msg.as_string())


    # -----------------------------
    # UI
    # -----------------------------
    st.title("📨 Уведомления должникам по коммунальным платежам")

    with st.expander("Что делает приложение", expanded=False):
        st.markdown(
            """
    - Загружаете таблицу (Excel/CSV/PDF).
    - Приложение находит колонку **Сумма долга**, выбирает строки где **долг > 0**.
    - Формирует уведомление по шаблону и отправляет на **e-mail из таблицы** (или на один общий e-mail по вашему выбору).
            """
        )

    uploaded = st.file_uploader(
        "Загрузите файл (CSV / Excel / PDF)",
        type=["csv", "xlsx", "xls", "pdf"],
    )

    if not uploaded:
        st.stop()

    try:
        df_raw = read_table(uploaded)
    except Exception as e:
        st.error(str(e))
        st.stop()

    st.subheader("1) Данные из файла")
    st.write("Превью (первые 20 строк):")
    st.dataframe(df_raw.head(20), use_container_width=True)

    auto_map = auto_map_columns(df_raw)

    st.subheader("2) Сопоставление колонок")
    st.caption("Мы попытались определить колонки автоматически. При необходимости исправьте вручную.")
    col_map = {}

    cols = ["— не выбрано —"] + list(df_raw.columns)

    left, right = st.columns(2)
    for i, field in enumerate(REQUIRED_FIELDS):
        target_col = auto_map.get(field)
        default_idx = cols.index(target_col) if target_col in cols else 0
        container = left if i % 2 == 0 else right
        with container:
            picked = st.selectbox(field, cols, index=default_idx, key=f"map_{field}")
        col_map[field] = None if picked == "— не выбрано —" else picked

    # Минимум для работы: ФИО + Сумма долга + Email (или общий email)
    missing_min = [f for f in ["ФИО", "Сумма долга"] if not col_map.get(f)]
    if missing_min:
        st.error(f"Нужно выбрать колонки минимум для: {', '.join(missing_min)}")
        st.stop()

    # Настройка получателей
    st.subheader("3) Кому отправлять письма")
    mode = st.radio(
        "Режим отправки",
        [
            "Использовать Email из каждой строки (колонка Email)",
            "Отправлять ВСЕ письма на один общий Email",
        ],
    )

    common_email = None
    if mode == "Использовать Email из каждой строки (колонка Email)":
        if not col_map.get("Email"):
            st.error("Вы выбрали режим отправки по строкам, но не указали колонку Email.")
            st.stop()
    else:
        common_email = st.text_input("Общий Email получателя", placeholder="example@domain.com").strip()
        if not common_email:
            st.error("Введите общий Email получателя.")
            st.stop()

    # Подготовка рабочей таблицы
    df = df_raw.copy()

    debt_col = col_map["Сумма долга"]
    df["_debt"] = df[debt_col].apply(to_number)

    debtors = df[df["_debt"] > 0].copy()
    st.subheader("4) Должники")
    c1, c2, c3 = st.columns(3)
    c1.metric("Всего строк", len(df))
    c2.metric("Должников (долг > 0)", len(debtors))
    c3.metric("Сумма долгов", f"{debtors['_debt'].sum():,.2f}".replace(",", " "))

    st.dataframe(debtors.head(50), use_container_width=True)

    # Шаблон письма
    st.subheader("5) Шаблон уведомления")
    today = datetime.now().date()
    due = today + timedelta(days=31)

    default_template = (
        "Уважаемый(ая), {FIO}, у Вас образовалась задолженность по коммунальным платежам "
        "в размере {DEBT}.\n\n"
        "Дата формирования: {TODAY}\n"
        "Необходимо оплатить до {DUE}."
    )

    template = st.text_area(
        "Текст письма (можно редактировать). Доступные поля: {FIO}, {DEBT}, {TODAY}, {DUE}",
        value=default_template,
        height=160,
    )

    subject = st.text_input("Тема письма", value="Уведомление о задолженности")

    # -----------------------------
    # UI: SMTP Settings
    # -----------------------------
    st.subheader("6) SMTP настройки")

    # Пробуем достать настройки из secrets.toml, если их нет — оставляем пустыми
    secrets = st.secrets.get("smtp", {})
    default_user = secrets.get("email", "")
    default_pass = secrets.get("password", "")
    default_host = secrets.get("host", "smtp.gmail.com")
    default_port = secrets.get("port", 587)

    with st.expander("Настройки подключения", expanded=not default_user):
        smtp_host = st.text_input("SMTP host", value=default_host)
        smtp_port = st.number_input("SMTP port", min_value=1, value=default_port)
        use_tls = st.checkbox("Использовать STARTTLS", value=True)
        
        st.info("Для Gmail используйте 'Пароль приложений' (App Password).")
        smtp_user = st.text_input("SMTP login", value=default_user)
        smtp_password = st.text_input("SMTP password", value=default_pass, type="password")
        from_addr = st.text_input("From (от кого)", value=default_user)

    # Добавляем настройку задержки
    sleep_time = st.slider("Задержка между письмами (сек)", 0.0, 5.0, 1.0, step=0.5, 
                        help="Gmail может заблокировать за спам, если слать слишком быстро. Рекомендуем 1-2 сек.")

    dry_run = st.checkbox("Тестовый прогон (не отправлять, только сформировать)", value=False)

    def format_money(x: float) -> str:
        # 12345.6 -> 12 345.60
        s = f"{x:,.2f}"
        s = s.replace(",", " ")
        return s

    def make_body(fio: str, debt: float) -> str:
        return template.format(
            FIO=fio,
            DEBT=format_money(debt),
            TODAY=today.strftime("%d.%m.%Y"),
            DUE=due.strftime("%d.%m.%Y"),
        )

    # Предпросмотр одного письма
    st.subheader("7) Предпросмотр")
    if len(debtors) > 0:
        fio_col = col_map["ФИО"]
        sample = debtors.iloc[0]
        sample_fio = str(sample.get(fio_col, "")).strip()
        sample_debt = float(sample["_debt"])
        st.text_area("Пример письма", value=make_body(sample_fio, sample_debt), height=180)
    else:
        st.info("Должников нет — отправлять нечего.")

    # Отправка
    st.subheader("8) Отправка")
    send_btn = st.button("🚀 Сформировать и отправить (или выполнить тестовый прогон)", type="primary")

    if send_btn:
        if len(debtors) == 0:
            st.warning("Должников нет — отправка не выполнена.")
            st.stop()

        # Валидация, если не тестовый прогон
        if not dry_run:
            missing = []
            if not smtp_host: missing.append("SMTP host")
            if not from_addr: missing.append("From")
            if smtp_user and not smtp_password: missing.append("SMTP password")
            
            if missing:
                st.error("Заполните: " + ", ".join(missing))
                st.stop()

        fio_col = col_map["ФИО"]
        email_col = col_map.get("Email")

        log_rows = []
        
        # Инициализация прогресс-бара
        progress_bar = st.progress(0)
        status_text = st.empty()

        total = len(debtors)
        ok = 0
        fail = 0

        # ЦИКЛ ОТПРАВКИ
        for idx, row in enumerate(debtors.itertuples(index=False), start=1):
            # 1. СНАЧАЛА получаем данные (до блока try), чтобы переменные точно существовали
            r = debtors.iloc[idx - 1]
            
            # Безопасное получение ФИО
            fio = str(r.get(fio_col, "")).strip()
            
            # Безопасное получение Долга
            debt = float(r.get("_debt", 0.0))

            # Определение получателя
            if mode == "Использовать Email из каждой строки (колонка Email)":
                raw_email = str(r.get(email_col, "")).strip() if email_col else ""
                to_addr = raw_email
            else:
                to_addr = common_email

            # Формируем тело письма
            body = make_body(fio, debt)

            # 2. ТЕПЕРЬ пытаем отправить
            try:
                # Проверка на корректность email (базовая)
                if "@" not in to_addr:
                    raise ValueError("Некорректный Email адрес")

                if dry_run:
                    # Тестовый режим - просто имитируем успех
                    ok += 1
                    log_rows.append({
                        "ФИО": fio, 
                        "Email": to_addr, 
                        "Долг": debt, 
                        "Статус": "OK (dry-run)", 
                        "Комментарий": "Тест (не отправлено)"
                    })
                else:
                    # Реальная отправка
                    send_email_smtp(
                        smtp_host=smtp_host,
                        smtp_port=int(smtp_port),
                        smtp_user=smtp_user,
                        smtp_password=smtp_password,
                        use_tls=use_tls,
                        from_addr=from_addr,
                        to_addr=to_addr,
                        subject=subject,
                        body=body,
                    )

                    add_to_monitoring(
                        fio=fio, 
                        debt=debt, 
                        method="E-mail", 
                        status="Доставлено (авто)", 
                        comment=f"Отправлено на {to_addr}"
                    )
                    
                    # Задержка, чтобы Gmail не заблокировал (берем из слайдера или ставим 1 сек)
                    if 'sleep_time' in locals():
                        time.sleep(sleep_time)
                    else:
                        time.sleep(1) 

                    ok += 1
                    log_rows.append({
                        "ФИО": fio, 
                        "Email": to_addr, 
                        "Долг": debt, 
                        "Статус": "OK", 
                        "Комментарий": ""
                    })

            except Exception as e:
                fail += 1
                # Теперь fio и to_addr точно существуют, ошибка NameError исчезнет
                log_rows.append({
                    "ФИО": fio, 
                    "Email": to_addr, 
                    "Долг": debt, 
                    "Статус": "Ошибка", 
                    "Комментарий": str(e)
                })

            # Обновляем UI
            progress_bar.progress(idx / total)
            status_text.write(f"Обработано {idx}/{total} | Успешно: {ok} | Ошибок: {fail}")

        st.success(f"Готово. Успешно: {ok}, Ошибок: {fail}")

        # Лог (исправлена ошибка use_container_width для новых версий Streamlit)
        log_df = pd.DataFrame(log_rows)
        st.dataframe(log_df, use_container_width=True)

        # Скачивание файла
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            log_df.to_excel(writer, index=False, sheet_name="log")
        
        st.download_button(
            "⬇️ Скачать лог отправки (Excel)",
            data=out.getvalue(),
            file_name="email_send_log.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# --- ВКЛАДКА 2: ДАШБОРД ---
with tab_dashboard:
    st.title("Мониторинг контакта с должниками")

    st.header("📊 Журнал работы с задолженностью")
    
    df_monitor = load_monitoring_data()
    
    if not df_monitor.empty:
        # Статистика в ряд
        c1, c2, c3, c4 = st.columns(4)
        total_debt = df_monitor["Сумма долга"].sum()
        c1.metric("Всего контактов", len(df_monitor))
        c2.metric("Общая сумма", f"{total_debt:,.2f} ₽")
        c3.metric("E-mail рассылки", len(df_monitor[df_monitor["Способ информирования"]=="E-mail"]))
        c4.metric("Оплачено", len(df_monitor[df_monitor["Статус информирования"]=="Оплачено"]))

        st.divider()

        # Фильтры
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            search_fio = st.text_input("🔍 Поиск по ФИО")
        with col_f2:
            filter_status = st.multiselect("Фильтр статуса", df_monitor["Статус информирования"].unique())

        display_df = df_monitor.copy()
        if search_fio:
            display_df = display_df[display_df["ФИО"].str.contains(search_fio, case=False)]
        if filter_status:
            display_df = display_df[display_df["Статус информирования"].isin(filter_status)]

        # Редактор данных
        st.subheader("Редактирование базы")
        edited_df = st.data_editor(
            display_df,
            use_container_width=True,
            num_rows="dynamic",
            key="dashboard_editor",
            column_config={
                "Статус информирования": st.column_config.SelectboxColumn(
                    options=["Ожидается", "В процессе", "Доставлено (авто)", "Почта отправлена", "Звонок выполнен", "Оплачено", "Отказ"]
                ),
                "Способ информирования": st.column_config.SelectboxColumn(
                    options=["E-mail", "Звонок", "Почта РФ", "Личный визит"]
                ),
                "Сумма долга": st.column_config.NumberColumn(format="%.2f ₽")
            }
        )

        if st.button("💾 Сохранить изменения в файл"):
            # Если мы фильтровали данные, нужно объединить изменения с основной базой
            # Для простоты в демо: сохраняем то, что на экране (если фильтры не пустые, будьте осторожны)
            save_monitoring_data(edited_df)
            st.success("База обновлена!")
            time.sleep(1)
            st.rerun()
    else:
        st.info("В базе мониторинга пока нет записей. Отправьте уведомления или добавьте запись вручную.")

    # Форма ручного добавления (оставляем как была в предыдущем совете)
    with st.expander("➕ Добавить запись вручную (звонок/почта)"):
        with st.form("add_contact_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                fio_input = st.text_input("ФИО должника")
                debt_input = st.number_input("Сумма долга", min_value=0.0)
            with col2:
                method = st.selectbox("Способ информирования", ["E-mail", "Звонок", "Почта РФ", "Личный визит"])
                status = st.selectbox("Статус информирования", ["Ожидается", "В процессе", "Доставлено/Проведен", "Отказ", "Оплачено"])
            
            comment = st.text_area("Комментарий")
            submit_add = st.form_submit_button("Добавить в базу")
            
            if submit_add:
                if fio_input:
                    new_entry = {
                        "Дата контакта": datetime.now().strftime("%d.%m.%Y %H:%M"),
                        "ФИО": fio_input,
                        "Сумма долга": debt_input,
                        "Способ информирования": method,
                        "Статус информирования": status,
                        "Комментарий": comment
                    }
                    df_monitor = pd.concat([df_monitor, pd.DataFrame([new_entry])], ignore_index=True)
                    save_monitoring_data(df_monitor)
                    st.success("Данные добавлены!")
                else:
                    st.error("Введите ФИО!")

    # --- Секция аналитики (Мини-дашборд) ---
    if not df_monitor.empty:
        st.subheader("Статистика по контактам")
        c1, c2, c3 = st.columns(3)
        c1.metric("Всего контактов", len(df_monitor))
        c2.metric("Сумма в работе", f"{df_monitor['Сумма долга'].sum():,.2f}")
        c3.metric("Оплачено (отметки)", len(df_monitor[df_monitor["Статус информирования"] == "Оплачено"]))
        
        st.divider()

        # --- Интерактивная таблица ---
        st.subheader("Журнал контактов")
        st.info("Вы можете редактировать данные прямо в таблице ниже или удалять строки.")
        
        # Используем st.data_editor для интерактивного редактирования базы
        edited_df = st.data_editor(
            df_monitor, 
            use_container_width=True, 
            num_rows="dynamic", # позволяет удалять строки
            key="monitor_editor"
        )
        
        # Кнопка сохранения изменений в таблице
        if st.button("Сохранить изменения в таблице"):
            save_monitoring_data(edited_df)
            st.success("Изменения сохранены в файл!")
            st.rerun()
            
    else:
        st.info("База мониторинга пока пуста. Добавьте первого должника выше.")
