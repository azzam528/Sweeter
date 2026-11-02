# Sweeter

**A Simple Social Media Web Application Built with Flask and MongoDB**

Sweeter is a social media web application that allows users to share posts, interact with other users, and participate in conversations through comments and nested replies.

The application is built with Python Flask, MongoDB, and a lightweight frontend using HTML, CSS, JavaScript, jQuery, and Bulma CSS.

## Features

### Authentication
- User registration and login.
- JWT-based authentication.
- Authentication using HTTP cookies.
- Protected endpoints for authenticated users.

### User Profile
- User profile information.
- Profile picture support.
- User-specific content.

### Posts
- Create and display posts.
- Dynamic post rendering.
- Display posts in the main feed.

### Reactions
- Like posts.
- Star posts.
- Thumb reactions.

### Comment & Reply System
- Create comments on posts.
- Reply to existing comments.
- Nested replies with unlimited database hierarchy depth.
- Collapsible comment sections for each post.
- Real-time comment counter updates.
- Soft deletion to preserve conversation history.
- Comment ownership authorization.
- XSS protection for dynamically rendered content.

### Database
- MongoDB as the primary database.
- PyMongo for database operations.
- Database indexes for frequently queried comment data.
- Batch aggregation for comment counting in the post feed.

## Tech Stack

| Category | Technology |
|---|---|
| Backend | Python, Flask |
| Database | MongoDB |
| Database Driver | PyMongo |
| Authentication | JWT (HS256) |
| Frontend | HTML, CSS, JavaScript |
| CSS Framework | Bulma CSS |
| JavaScript Library | jQuery |
| Configuration | Environment Variables |

## Project Structure

```text
Sweeter/
├── app.py
├── database.py
├── requirements.txt
├── .env.example
├── .gitignore
│
├── docs/
│   ├── comment-system.md
│   └── security-configuration.md
│
├── static/
│   ├── myjs.js
│   └── mystyle.css
│
└── templates/
    ├── index.html
    ├── user.html
    └── ...
```

*Note: This is a simplified overview. Actual files and directories may differ depending on the current project structure.*

## Getting Started

Follow these instructions to run Sweeter locally.

### Prerequisites

Make sure you have installed:

- Python 3.10 or newer.
- MongoDB server or an accessible MongoDB instance.
- Git.
- pip.

### 1. Clone the Repository

```bash
git clone https://github.com/azzam528/Sweeter.git
cd Sweeter
```

### 2. Create a Virtual Environment

Windows:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

Linux / macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root directory.

Use `.env.example` as a reference if it is available.

Configure the required values according to the environment variable names used in the application.

**Important:** Never commit your `.env` file or expose database credentials and JWT secrets in a public repository.

For JWT HS256, use a cryptographically secure secret with at least 32 bytes of key material.

### 5. Configure MongoDB

Make sure MongoDB is running and that the application has the correct database connection configuration.

Verify that the database connection settings match the configuration expected by `database.py`.

### 6. Run the Application

```bash
python app.py
```

Open the local address configured by Flask in your browser.

By default, Flask development applications commonly use:

```text
http://127.0.0.1:5000
```

## Comment System API

The comment system provides the following endpoints:

| Method | Endpoint | Description |
|---|---|---|
| POST | `/create_comment` | Create a comment or reply |
| GET | `/get_comments` | Retrieve comments for a post |
| POST | `/delete_comment` | Soft-delete an owned comment |

### Create a Comment

`POST /create_comment`

Form parameters:

- `post_id_give`: Target post ID.
- `content_give`: Comment content.
- `parent_comment_id_give`: Parent comment ID. Use an empty value for a root comment.

Successful response: `201 Created`.

### Get Comments

`GET /get_comments?post_id_give=POST_ID`

Retrieves comments associated with the specified post.

### Delete a Comment

`POST /delete_comment`

Form parameter:

- `comment_id_give`: ID of the comment to delete.

The application uses soft deletion so that existing replies remain connected to their parent comment.

## Security Considerations

Sweeter implements several security measures:

- JWT-based authentication.
- Server-side identity verification.
- Comment ownership authorization.
- Input validation.
- XSS protection when rendering dynamic content.
- Avatar URL protocol validation.
- Soft deletion for comments.
- Environment-based secret management.

Before deploying to production:

- Generate a strong and unique `SECRET_KEY`.
- Configure secure cookie attributes.
- Use HTTPS.
- Keep `.env` and credentials out of version control.
- Configure MongoDB authentication and network access restrictions.
- Run the application using a production WSGI server such as Gunicorn.
- Review production logging and error handling.

For more details, see `docs/security-configuration.md` and `docs/comment-system.md`.

## Documentation

Additional technical documentation:

- `docs/comment-system.md` — Comment system architecture, database design, API endpoints, and frontend implementation.
- `docs/security-configuration.md` — Security audit and environment configuration guide.

## Development

Sweeter is developed incrementally, with attention to:

- Maintainable backend code.
- Consistent API behavior.
- Database query efficiency.
- Frontend usability.
- Authentication and authorization.
- Security and application reliability.

## License

No license has been specified yet.

If you intend to make this project open source, consider adding a `LICENSE` file that defines how others may use, modify, and distribute the project.

---

**Sweeter** — A simple place to share thoughts and connect through conversations.