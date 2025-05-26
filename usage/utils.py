from pathlib import Path
from IPython.display import Markdown, display


def display_bash_script(script_path: str | Path):
    script_path = Path(script_path)
    if not script_path.exists():
        raise FileNotFoundError(f"Script file {script_path} does not exist.")
    script = script_path.read_text()
    markdown_script = f"```bash\n{script}\n```"
    display(Markdown(markdown_script))


from IPython.display import display, Markdown
from pathlib import Path


def display_sql_script(script_path):
    """
    Display SQL script content in a nicely formatted markdown code block.
    
    Args:
        script_path (Path or str): Path to the SQL script file
    """
    script_path = Path(script_path)

    if not script_path.exists():
        raise FileNotFoundError(f"Script file {script_path} does not exist.")
    with open(script_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Create markdown with SQL syntax highlighting
    markdown_content = f"```sql\n{content}\n```"
    display(Markdown(markdown_content))
