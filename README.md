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


**Sweeter** — A simple place to share thoughts and connect through conversations.
