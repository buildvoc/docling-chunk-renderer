from dash import Dash, html


app = Dash(__name__)
app.layout = html.Div("Dash test OK")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8051, debug=False)
