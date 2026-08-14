from flask import Flask, request, session, jsonify

app = Flask(__name__)


@app.route("/users", methods=["GET"])
def list_users():
    """List all users, optionally filtered by role."""
    role = request.args.get("role")
    return jsonify([])


@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    """Fetch a single user by id."""
    return jsonify({"id": user_id})


@app.route("/users", methods=["POST"])
def create_user():
    """Create a new user from a JSON payload."""
    data = request.get_json()
    return jsonify(data), 201


@app.route("/profile", methods=["GET"])
def profile():
    """Return the profile of the logged-in user (session required)."""
    uid = session.get("user_id")
    return jsonify({"id": uid})


@app.post("/logout")
def logout():
    session["user_id"] = None
    return "", 204


if __name__ == "__main__":
    app.run()
