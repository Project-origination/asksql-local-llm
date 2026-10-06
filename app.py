import re
import sqlite3
import time

import pandas as pd
import plotly.express as px
import streamlit as st
from openai import OpenAI


# =========================================================
# PAGE
# =========================================================

st.set_page_config(
    page_title="AskSQL",
    page_icon="💬",
    layout="wide",
)


# =========================================================
# LOCAL LLM
# =========================================================

client = OpenAI(
    base_url="http://127.0.0.1:1234/v1",
    api_key="lm-studio",
)

MODEL = "qwen/qwen3-4b-2507"


# =========================================================
# DATABASE SCHEMA
# =========================================================

schema = """
customers(
    customer_id INTEGER,
    customer_name TEXT,
    country TEXT,
    segment TEXT
)

sales(
    sale_id INTEGER,
    sale_date TEXT,
    customer_id INTEGER,
    product TEXT,
    revenue REAL,
    cost REAL
)

Relationship:
sales.customer_id = customers.customer_id
"""


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
You are a senior SQL data analyst.

Convert business questions into valid SQLite queries.

Rules:
- Use only the provided schema.
- Generate SELECT or WITH queries only.
- Never modify the database.
- Use JOIN only when required by the requested fields or filters.
- Return SQL only.
- No markdown.
- No explanation.
- When using SUM, AVG, COUNT, MIN or MAX,
  every selected non-aggregated column must be included in GROUP BY.
- When ranking aggregated results, give the metric a clear alias
  such as total_revenue, total_profit or total_margin.
- When answering highest, lowest, top, bottom or ranking questions,
  ALWAYS include the metric used for ranking in the SELECT output.
- Include the business dimension and the metric that supports the answer.
- Prefer simple and efficient SQL.

Example:

Question:
Which country generated the most revenue?

Correct SQL:

SELECT
    c.country,
    SUM(s.revenue) AS total_revenue
FROM customers c
JOIN sales s
    ON c.customer_id = s.customer_id
GROUP BY c.country
ORDER BY total_revenue DESC
LIMIT 1;
"""


# =========================================================
# LLM FUNCTIONS
# =========================================================

def generate_sql(question):

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"""
Database schema:

{schema}

Business question:

{question}
""",
            },
        ],
        temperature=0,
    )

    return response.choices[0].message.content


def repair_sql(question, failed_sql, issue):

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"""
The SQL query below needs to be corrected.

Original business question:

{question}

Database schema:

{schema}

Current SQL:

{failed_sql}

Problem:

{issue}

Generate a corrected SQLite query.

Return SQL only.
""",
            },
        ],
        temperature=0,
    )

    return response.choices[0].message.content


# =========================================================
# SQL SECURITY
# =========================================================

def validate_sql(sql):

    sql = (
        sql
        .strip()
        .replace("```sql", "")
        .replace("```SQL", "")
        .replace("```", "")
        .strip()
    )

    sql_without_last_semicolon = sql.rstrip(";")

    if ";" in sql_without_last_semicolon:
        raise ValueError(
            "Multiple SQL statements are not allowed."
        )

    upper_sql = sql.upper()

    if not (
        upper_sql.startswith("SELECT")
        or upper_sql.startswith("WITH")
    ):
        raise ValueError(
            "Only SELECT or WITH queries are allowed."
        )

    forbidden = [
        "DELETE",
        "UPDATE",
        "INSERT",
        "DROP",
        "ALTER",
        "CREATE",
        "PRAGMA",
        "ATTACH",
        "DETACH",
        "VACUUM",
        "REPLACE",
    ]

    for keyword in forbidden:

        if re.search(
            rf"\b{keyword}\b",
            upper_sql
        ):
            raise ValueError(
                f"Forbidden SQL keyword detected: {keyword}"
            )

    return sql


# =========================================================
# SEMANTIC VALIDATION
# =========================================================

def is_ranking_question(question):

    ranking_terms = [
        "highest",
        "lowest",
        "most",
        "least",
        "top",
        "bottom",
        "best",
        "worst",
        "le plus",
        "la plus",
        "les plus",
        "le moins",
        "la moins",
        "meilleur",
        "meilleure",
        "plus élevé",
        "plus élevée",
        "moins élevé",
        "moins élevée",
    ]

    question_lower = question.lower()

    return any(
        term in question_lower
        for term in ranking_terms
    )


# =========================================================
# DATABASE EXECUTION
# =========================================================

def execute_query(question, sql):

    repaired = False
    repair_reason = None

    for attempt in range(2):

        safe_sql = validate_sql(sql)

        connection = sqlite3.connect(
            "file:sales.db?mode=ro",
            uri=True,
        )

        try:

            result = pd.read_sql_query(
                safe_sql,
                connection,
            )

            # Ranking question should return
            # the entity + the ranking metric.
            if (
                attempt == 0
                and is_ranking_question(question)
                and len(result.columns) < 2
            ):

                repaired = True

                repair_reason = (
                    "The initial query returned the ranked entity "
                    "without the metric supporting the answer."
                )

                sql = repair_sql(
                    question,
                    safe_sql,
                    """
This is a ranking question.

The SELECT output must contain:
1. the entity being ranked
2. the numeric metric used for ranking

Examples:
country + total_revenue
customer_name + total_profit
product + total_margin
""",
                )

                continue

            return (
                safe_sql,
                result,
                repaired,
                repair_reason,
            )

        except Exception as error:

            if attempt == 0:

                repaired = True

                repair_reason = (
                    "The first generated query caused a SQLite error."
                )

                sql = repair_sql(
                    question,
                    safe_sql,
                    str(error),
                )

            else:
                raise error

        finally:
            connection.close()

    raise RuntimeError(
        "Unable to produce a valid query after one correction."
    )


# =========================================================
# DISPLAY HELPERS
# =========================================================

def pretty_name(column):

    return (
        column
        .replace("_", " ")
        .strip()
        .title()
    )


def format_value(value):

    if pd.isna(value):
        return "N/A"

    if isinstance(value, (int, float)):

        if float(value).is_integer():
            return f"{int(value):,}"

        return f"{value:,.2f}"

    return str(value)


def formatted_dataframe(df):

    display = df.copy()

    display.columns = [
        pretty_name(column)
        for column in display.columns
    ]

    for column in display.columns:

        if pd.api.types.is_numeric_dtype(
            display[column]
        ):

            display[column] = display[column].map(
                format_value
            )

    return display


def detect_time_column(df):

    candidates = [
        "month",
        "date",
        "year",
        "quarter",
        "time",
        "period",
    ]

    for column in df.columns:

        column_lower = column.lower()

        if any(
            candidate in column_lower
            for candidate in candidates
        ):
            return column

    return None


def create_visualization(df):

    if len(df) < 2:
        return None

    numeric_columns = [
        column
        for column in df.columns
        if pd.api.types.is_numeric_dtype(
            df[column]
        )
    ]

    if not numeric_columns:
        return None

    categorical_columns = [
        column
        for column in df.columns
        if column not in numeric_columns
    ]

    if not categorical_columns:
        return None

    dimension = categorical_columns[0]
    metric = numeric_columns[0]

    time_column = detect_time_column(df)

    # -----------------------------------------
    # TIME SERIES
    # -----------------------------------------

    if time_column:

        chart_data = df.copy()

        try:
            chart_data[time_column] = pd.to_datetime(
                chart_data[time_column]
            )

            chart_data = chart_data.sort_values(
                time_column
            )

        except Exception:
            pass

        figure = px.line(
            chart_data,
            x=time_column,
            y=metric,
            markers=True,
            title=f"{pretty_name(metric)} over time",
        )

        figure.update_traces(
            line_width=3,
            marker_size=8,
        )

    # -----------------------------------------
    # CATEGORY COMPARISON
    # -----------------------------------------

    else:

        chart_data = df.sort_values(
            metric,
            ascending=False,
        )

        figure = px.bar(
            chart_data,
            x=dimension,
            y=metric,
            text_auto=".3s",
            title=(
                f"{pretty_name(metric)} "
                f"by {pretty_name(dimension)}"
            ),
        )

    figure.update_layout(
        height=430,
        margin=dict(
            l=20,
            r=20,
            t=60,
            b=20,
        ),
        xaxis_title=pretty_name(
            time_column or dimension
        ),
        yaxis_title=pretty_name(metric),
        showlegend=False,
    )

    return figure


# =========================================================
# DETERMINISTIC QUICK INSIGHT
# =========================================================

def build_insight(df):

    if df.empty:
        return None

    numeric_columns = [
        column
        for column in df.columns
        if pd.api.types.is_numeric_dtype(
            df[column]
        )
    ]

    categorical_columns = [
        column
        for column in df.columns
        if column not in numeric_columns
    ]

    if not numeric_columns:
        return None

    metric = numeric_columns[0]

    if categorical_columns:

        dimension = categorical_columns[0]

        top_row = df.loc[
            df[metric].idxmax()
        ]

        if detect_time_column(df):

            return (
                f"The highest {pretty_name(metric).lower()} "
                f"is observed in "
                f"**{top_row[dimension]}**, "
                f"with **{format_value(top_row[metric])}**."
            )

        return (
            f"**{top_row[dimension]}** ranks first for "
            f"{pretty_name(metric).lower()}, "
            f"with **{format_value(top_row[metric])}**."
        )

    return None


# =========================================================
# LM STUDIO STATUS
# =========================================================

def lm_studio_available():

    try:

        models = client.models.list()

        return MODEL in [
            model.id
            for model in models.data
        ]

    except Exception:
        return False


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.title("💬 AskSQL")

    st.caption(
        "Conversational analytics powered by a local LLM."
    )

    if lm_studio_available():
        st.success("Local AI connected")
    else:
        st.error("LM Studio offline")

    st.divider()

    st.markdown("**Model**")
    st.caption("Qwen3 4B 2507")

    st.markdown("**Inference**")
    st.caption("Local • LM Studio")

    st.markdown("**Database**")
    st.caption("SQLite • Read-only")

    st.markdown("**Language**")
    st.caption("French & English")

    st.divider()

    st.caption(
        "No cloud LLM API is required."
    )


# =========================================================
# HEADER
# =========================================================

st.title("💬 AskSQL")

st.markdown(
    """
### Conversational analytics, powered locally

Ask a business question in **French or English**.
AskSQL converts it into SQL, validates the query,
executes it safely and visualizes the result.
"""
)


badge_1, badge_2, badge_3, badge_4 = st.columns(4)

badge_1.info("🧠 Local AI")
badge_2.info("🔒 Read-only")
badge_3.info("🌍 FR / EN")
badge_4.info("♻️ Auto-repair")


# =========================================================
# EXAMPLES
# =========================================================

st.markdown("### Try an example")

examples = [
    "Quel client a généré le plus de chiffre d'affaires en 2026 ?",
    "Quel produit a généré la marge totale la plus élevée en 2026 ?",
    "Montre-moi le chiffre d'affaires total par pays.",
]

if "question" not in st.session_state:
    st.session_state.question = ""

example_columns = st.columns(3)

for index, example in enumerate(examples):

    if example_columns[index].button(
        example,
        use_container_width=True,
    ):

        st.session_state.question = example
        st.rerun()


# =========================================================
# QUESTION
# =========================================================

st.markdown("### Ask your database")

question = st.text_input(
    "Business question",
    key="question",
    placeholder=(
        "Example: Show monthly revenue for 2026"
    ),
    label_visibility="collapsed",
)

analyze = st.button(
    "Analyze",
    type="primary",
    use_container_width=True,
)


# =========================================================
# ANALYSIS
# =========================================================

if analyze:

    if not question.strip():

        st.warning(
            "Enter a business question first."
        )

    elif not lm_studio_available():

        st.error(
            "LM Studio is offline. "
            "Start the local server and try again."
        )

    else:

        try:

            start_time = time.perf_counter()

            with st.spinner(
                "Qwen is translating your question into SQL..."
            ):

                generated_sql = generate_sql(
                    question
                )

                (
                    sql,
                    result,
                    repaired,
                    repair_reason,
                ) = execute_query(
                    question,
                    generated_sql,
                )

            elapsed = (
                time.perf_counter()
                - start_time
            )


            # =================================================
            # EXECUTION STATUS
            # =================================================

            st.markdown("### Query status")

            status_1, status_2, status_3 = st.columns(3)

            status_1.metric(
                "Rows returned",
                len(result),
            )

            status_2.metric(
                "Response time",
                f"{elapsed:.1f} s",
            )

            status_3.metric(
                "Auto-correction",
                "Used" if repaired else "Not needed",
            )

            if repaired:

                st.info(
                    f"AskSQL automatically refined the query. "
                    f"{repair_reason}"
                )


            # =================================================
            # RESULT
            # =================================================

            st.markdown("## Result")

            if result.empty:

                st.warning(
                    "No matching data was found."
                )

            elif len(result) == 1:

                result_columns = st.columns(
                    len(result.columns)
                )

                row = result.iloc[0]

                for ui_column, column_name in zip(
                    result_columns,
                    result.columns,
                ):

                    ui_column.metric(
                        pretty_name(column_name),
                        format_value(
                            row[column_name]
                        ),
                    )

                with st.expander(
                    "View result table"
                ):

                    st.dataframe(
                        formatted_dataframe(result),
                        use_container_width=True,
                        hide_index=True,
                    )

            else:

                st.dataframe(
                    formatted_dataframe(result),
                    use_container_width=True,
                    hide_index=True,
                )


            # =================================================
            # QUICK INSIGHT
            # =================================================

            insight = build_insight(result)

            if insight:

                st.markdown("### Quick insight")

                st.success(insight)


            # =================================================
            # VISUALIZATION
            # =================================================

            figure = create_visualization(
                result
            )

            if figure is not None:

                st.markdown("### Visualization")

                st.plotly_chart(
                    figure,
                    use_container_width=True,
                )


            # =================================================
            # SQL
            # =================================================

            with st.expander(
                "🔍 View generated SQL"
            ):

                st.code(
                    sql,
                    language="sql",
                )


        except Exception as error:

            st.error(
                f"Unable to process the query: {error}"
            )


# =========================================================
# HOW IT WORKS
# =========================================================

st.divider()

st.markdown("## How it works")

step_1, step_2, step_3, step_4 = st.columns(4)

with step_1:

    st.markdown("### ① Ask")
    st.caption(
        "The user writes a business question "
        "in natural language."
    )

with step_2:

    st.markdown("### ② Generate")
    st.caption(
        "Qwen3 4B converts the business intent "
        "into SQLite."
    )

with step_3:

    st.markdown("### ③ Validate")
    st.caption(
        "Unsafe SQL is blocked and invalid "
        "queries can be repaired."
    )

with step_4:

    st.markdown("### ④ Analyze")
    st.caption(
        "SQLite returns the data and AskSQL "
        "visualizes the result."
    )


# =========================================================
# TECH STACK
# =========================================================

with st.expander("Technical architecture"):

    st.code(
        """
Business question
        ↓
Qwen3 4B
        ↓
SQL generation
        ↓
Security validation
        ↓
Semantic validation
        ↓
Auto-repair if required
        ↓
SQLite read-only
        ↓
Pandas
        ↓
Plotly / Streamlit
""",
        language="text",
    )

    st.markdown(
        """
**Stack**

Python • Qwen3 4B • LM Studio • SQLite •
Pandas • Plotly • Streamlit
"""
    )


# =========================================================
# FOOTER
# =========================================================

st.divider()

st.caption(
    "AskSQL • Local conversational analytics prototype"
)