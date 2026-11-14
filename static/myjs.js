// ==========================================
// SWEETER — CLIENT-SIDE JAVASCRIPT
// Features: Post, Reactions, Comment & Reply System
// ==========================================

// --- Helper Keamanan XSS & Sanitasi URL ---

function escapeHtml(str) {
  if (str === null || str === undefined) {
    return '';
  }
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeAvatarUrl(url) {
  if (!url || typeof url !== 'string') {
    return '/static/profile_pics/profile_placeholder.png';
  }
  url = url.trim();
  // Cegah protokol berbahaya seperti javascript:, data:, vbscript:
  if (/^(javascript|data|vbscript):/i.test(url)) {
    return '/static/profile_pics/profile_placeholder.png';
  }
  if (url.startsWith('/static/')) {
    return encodeURI(url);
  }
  if (url.startsWith('/')) {
    return encodeURI('/static' + url);
  }
  return encodeURI('/static/' + url);
}

// --- Helper Formatting Waktu & Angka ---

function time2str(date) {
  let today = new Date();
  let time = (today - date) / 1000 / 60;
  if (time < 60) {
    return parseInt(time) + ' minutes ago';
  }
  time = time / 60;
  if (time < 24) {
    return parseInt(time) + ' hours ago';
  }
  time = time / 24;
  if (time < 7) {
    return parseInt(time) + ' days ago';
  }
  let year = date.getFullYear();
  let month = date.getMonth() + 1;
  let day = date.getDate();

  return `${year}.${month}.${day}`;
}

function num2str(count) {
  if (count > 10000) {
    return parseInt(count / 1000) + 'k';
  }
  if (count > 500) {
    return parseInt(count / 100) / 10 + 'k';
  }
  if (count == 0) {
    return '';
  }
  return count;
}

// --- Handler Error AJAX Terpusat ---

function handle_ajax_error(xhr, defaultMsg) {
  if (xhr.status === 401) {
    alert('Sesi Anda telah berakhir atau belum login. Silakan login kembali.');
    window.location.href = '/login';
    return;
  }
  let errorMsg = defaultMsg;
  if (xhr.responseJSON && xhr.responseJSON.msg) {
    errorMsg = xhr.responseJSON.msg;
  } else if (xhr.responseText) {
    try {
      let res = JSON.parse(xhr.responseText);
      if (res.msg) errorMsg = res.msg;
    } catch (e) {}
  }
  alert(errorMsg);
}

// --- Posting Baru ---

function post() {
  let comment = $('#textarea-post').val();
  let today = new Date().toISOString();
  $.ajax({
    type: 'POST',
    url: '/posting',
    data: {
      comment_give: comment,
      date_give: today,
    },
    success: function (response) {
      $('#modal-post').removeClass('is-active');
      window.location.reload();
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal membuat postingan.');
    }
  });
}

// --- Pengambilan & Rendering Feed Postingan ---

function get_posts(username) {
  if (username == undefined) {
    username = '';
  }
  $('#post-box').empty();
  $.ajax({
    type: 'GET',
    url: `/get_posts?username_give=${username}`,
    data: {},
    success: function (response) {
      if (response['result'] === 'success') {
        let posts = response['posts'];
        for (let i = 0; i < posts.length; i++) {
          let post = posts[i];
          let time_post = new Date(post['date']);
          let time_before = time2str(time_post);
          let class_heart = post['heart_by_me'] ? 'fa-heart' : 'fa-heart-o';
          let class_stars = post['stars_by_me'] ? 'fa-star' : 'fa-star-o';
          let class_thumbs = post['thumbs_by_me'] ? 'fa-thumbs-up' : 'fa-thumbs-o-up';

          let count_comments = (post['count_comments'] !== undefined && post['count_comments'] !== null)
            ? post['count_comments']
            : 0;
          let display_comment_num = count_comments > 0 ? num2str(count_comments) : '0';

          let avatar_url = sanitizeAvatarUrl(post['profile_pic_real']);
          let profile_name_escaped = escapeHtml(post['profile_name']);
          let username_escaped = escapeHtml(post['username']);
          let comment_escaped = escapeHtml(post['comment']);

          let isStitch = $('body').hasClass('bg-background');
          let html_temp = '';

          if (isStitch) {
            html_temp = `
            <article class="bg-surface-container-lowest rounded-xl p-space-md shadow-sm transition-all hover:shadow-md mb-space-md" id="${post['_id']}">
              <!-- Post Header -->
              <div class="flex items-center justify-between mb-space-sm">
                <div class="flex items-center gap-space-sm min-w-0">
                  <a href="/user/${encodeURIComponent(post['username'])}" class="shrink-0">
                    <img class="w-10 h-10 rounded-full object-cover shrink-0" src="${avatar_url}" alt="${profile_name_escaped}" onerror="this.src='/static/profile_pics/profile_placeholder.png'"/>
                  </a>
                  <div class="flex flex-col min-w-0">
                    <div class="flex items-center gap-space-xs">
                      <a href="/user/${encodeURIComponent(post['username'])}" class="font-label-md text-label-md text-on-surface font-semibold truncate hover:underline no-underline text-inherit">${profile_name_escaped}</a>
                    </div>
                    <div class="flex items-center gap-space-xs text-on-surface-variant font-caption text-caption">
                      <span>@${username_escaped}</span>
                      <span>·</span>
                      <span>${time_before}</span>
                    </div>
                  </div>
                </div>
              </div>
              <!-- Post Body -->
              <p class="font-body-md text-body-md text-on-surface mb-space-md leading-relaxed whitespace-pre-wrap">
                ${comment_escaped}
              </p>
              <!-- Action Counter Toolbar -->
              <div class="flex items-center justify-between text-on-surface-variant font-label-sm text-label-sm mb-space-md pt-space-xs">
                <div class="flex items-center gap-space-md">
                  <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${post['heart_by_me'] ? 'text-primary font-bold' : ''}" aria-label="heart" onclick="toggle_like('${post['_id']}', 'heart')">
                    <span class="icon is-small">
                      <i class="fa ${class_heart}" aria-hidden="true"></i>
                    </span>&nbsp;<span class="like-num">${post['count_heart']}</span>
                  </a>
                  <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${post['stars_by_me'] ? 'text-primary font-bold' : ''}" aria-label="stars" onclick="toggle_stars('${post['_id']}', 'stars')">
                    <span class="icon is-small">
                      <i class="fa ${class_stars}" aria-hidden="true"></i>
                    </span>&nbsp;<span class="star-num">${num2str(post['count_stars'])}</span>
                  </a>
                  <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${post['thumbs_by_me'] ? 'text-primary font-bold' : ''}" aria-label="thumbs" onclick="toggle_thumbs('${post['_id']}', 'thumbs')">
                    <span class="icon is-small">
                      <i class="fa ${class_thumbs}" aria-hidden="true"></i>
                    </span>&nbsp;<span class="thumbs-num">${num2str(post['count_thumbs'])}</span>
                  </a>
                  <a class="flex items-center gap-1 text-primary-container font-semibold btn-toggle-comment cursor-pointer text-inherit no-underline" aria-label="comment" onclick="toggle_comments('${post['_id']}')" title="Komentar">
                    <span class="icon is-small">
                      <i class="fa fa-comment-o" aria-hidden="true"></i>
                    </span>&nbsp;<span class="comment-num" data-count="${count_comments}">${display_comment_num}</span>
                  </a>
                </div>
              </div>

              <!-- Container Komentar (Collapsible) -->
              <div class="comment-section is-hidden bg-surface-container-low rounded-lg p-space-md mt-space-sm" id="comments-section-${post['_id']}">
                <!-- Form Input Komentar Utama -->
                <div class="comment-form mb-3">
                  <div class="flex items-center gap-space-sm">
                    <div class="flex-1 relative flex items-center">
                      <input class="w-full bg-surface-container-lowest rounded-full py-space-xs pl-space-md pr-20 font-body-sm text-body-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary-container comment-input" id="input-comment-${post['_id']}" type="text" placeholder="Tulis komentar..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_comment('${post['_id']}');}" />
                      <button class="absolute right-1 px-space-sm py-1 bg-primary-container text-on-primary font-label-sm text-label-sm rounded-full hover:bg-primary transition-colors btn-submit-comment border-0 cursor-pointer" onclick="submit_comment('${post['_id']}')">
                        Reply
                      </button>
                    </div>
                  </div>
                </div>

                <!-- Container List Komentar (Tree) -->
                <div class="comment-list" id="comments-list-${post['_id']}"></div>
              </div>
            </article>
            `;
          } else {
            html_temp = `
            <div class="box" id="${post['_id']}">
              <article class="media">
                <div class="media-left">
                  <a class="image is-64x64" href="/user/${encodeURIComponent(post['username'])}">
                    <img class="is-rounded" src="${avatar_url}" alt="${profile_name_escaped}"/>
                  </a>
                </div>
                <div class="media-content">
                  <div class="content">
                    <p>
                      <strong>${profile_name_escaped}</strong> <small>@${username_escaped}</small>
                      <small>${time_before}</small>
                      <br/>
                      ${comment_escaped}
                    </p>
                  </div>
                  <nav class="level is-mobile mb-2">
                    <div class="level-left"> 
                      <a class="level-item is-sparta" aria-label="heart" onclick="toggle_like('${post['_id']}', 'heart')">
                        <span class="icon is-small">
                          <i class="fa ${class_heart}" aria-hidden="true"></i>
                        </span>&nbsp;<span class="like-num">${post['count_heart']}</span>
                      </a>
                      <a class="level-item is-sparta" aria-label="stars" onclick="toggle_stars('${post['_id']}', 'stars')">
                        <span class="icon is-small">
                          <i class="fa ${class_stars}" aria-hidden="true"></i>
                        </span>&nbsp;<span class="star-num">${num2str(post['count_stars'])}</span>
                      </a>
                      <a class="level-item is-sparta" aria-label="thumbs" onclick="toggle_thumbs('${post['_id']}', 'thumbs')">
                        <span class="icon is-small">
                          <i class="fa ${class_thumbs}" aria-hidden="true"></i>
                        </span>&nbsp;<span class="thumbs-num">${num2str(post['count_thumbs'])}</span>
                      </a>
                      <a class="level-item is-sparta btn-toggle-comment" aria-label="comment" onclick="toggle_comments('${post['_id']}')" title="Komentar">
                        <span class="icon is-small">
                          <i class="fa fa-comment-o" aria-hidden="true"></i>
                        </span>&nbsp;<span class="comment-num" data-count="${count_comments}">${display_comment_num}</span>
                      </a>
                    </div>
                  </nav>

                  <!-- Container Komentar (Collapsible) -->
                  <div class="comment-section is-hidden" id="comments-section-${post['_id']}">
                    <hr class="comment-divider"/>
                    
                    <!-- Form Input Komentar Utama -->
                    <div class="comment-form mb-3">
                      <div class="field has-addons">
                        <div class="control is-expanded">
                          <input class="input is-small is-rounded comment-input" id="input-comment-${post['_id']}" type="text" placeholder="Tulis komentar..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_comment('${post['_id']}');}" />
                        </div>
                        <div class="control">
                          <button class="button is-small is-sparta is-rounded btn-submit-comment" onclick="submit_comment('${post['_id']}')">
                            <span class="icon is-small"><i class="fa fa-paper-plane"></i></span>
                            <span>Kirim</span>
                          </button>
                        </div>
                      </div>
                    </div>

                    <!-- Container List Komentar (Tree) -->
                    <div class="comment-list" id="comments-list-${post['_id']}"></div>
                  </div>

                </div>
              </article>
            </div>
            `;
          }
          $('#post-box').append(html_temp);
        }
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memuat postingan.');
    }
  });
}

// --- Reaction System (Like / Stars / Thumbs) ---

function toggle_like(post_id, type) {
  let $a_like = $(`#${post_id} a[aria-label='heart']`);
  let $i_like = $a_like.find('i');

  if ($i_like.hasClass('fa-heart')) {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'unlike',
      },
      success: function (response) {
        $i_like.addClass('fa-heart-o').removeClass('fa-heart');
        $a_like.removeClass('text-primary font-bold');
        $a_like.find('span.like-num').text(num2str(response['count']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  } else {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'like',
      },
      success: function (response) {
        $i_like.addClass('fa-heart').removeClass('fa-heart-o');
        $a_like.addClass('text-primary font-bold');
        $a_like.find('span.like-num').text(num2str(response['count']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  }
}

function toggle_stars(post_id, type) {
  let $a_stars = $(`#${post_id} a[aria-label='stars']`);
  let $i_stars = $a_stars.find('i');

  if ($i_stars.hasClass('fa-star')) {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'unstars',
      },
      success: function (response) {
        $i_stars.addClass('fa-star-o').removeClass('fa-star');
        $a_stars.removeClass('text-primary font-bold');
        $a_stars.find('span.star-num').text(num2str(response['count_stars']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  } else {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'stars',
      },
      success: function (response) {
        $i_stars.addClass('fa-star').removeClass('fa-star-o');
        $a_stars.addClass('text-primary font-bold');
        $a_stars.find('span.star-num').text(num2str(response['count_stars']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  }
}

function toggle_thumbs(post_id, type) {
  let $a_thumbs = $(`#${post_id} a[aria-label='thumbs']`);
  let $i_thumbs = $a_thumbs.find('i');

  if ($i_thumbs.hasClass('fa-thumbs-up')) {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'unthumbs',
      },
      success: function (response) {
        $i_thumbs.addClass('fa-thumbs-o-up').removeClass('fa-thumbs-up');
        $a_thumbs.removeClass('text-primary font-bold');
        $a_thumbs.find('span.thumbs-num').text(num2str(response['count_thumbs']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  } else {
    $.ajax({
      type: 'POST',
      url: '/update_like',
      data: {
        post_id_give: post_id,
        type_give: type,
        action_give: 'thumbs',
      },
      success: function (response) {
        $i_thumbs.addClass('fa-thumbs-up').removeClass('fa-thumbs-o-up');
        $a_thumbs.addClass('text-primary font-bold');
        $a_thumbs.find('span.thumbs-num').text(num2str(response['count_thumbs']));
      },
      error: function (xhr) {
        handle_ajax_error(xhr, 'Gagal memperbarui reaksi.');
      }
    });
  }
}

// ==========================================
// COMMENT & REPLY SYSTEM FUNCTIONS
// ==========================================

// --- Toggle & Load Comment Section ---

function toggle_comments(post_id) {
  let $section = $(`#comments-section-${post_id}`);
  if ($section.hasClass('is-hidden')) {
    $section.removeClass('is-hidden');
    let $list = $(`#comments-list-${post_id}`);
    if (!$list.attr('data-loaded')) {
      load_comments(post_id, true);
    }
  } else {
    $section.addClass('is-hidden');
  }
}

function load_comments(post_id, showLoading = true) {
  let $list = $(`#comments-list-${post_id}`);
  if (showLoading) {
    $list.html(`
      <div class="has-text-centered py-3 comment-loading">
        <span class="icon is-small has-text-grey"><i class="fa fa-spinner fa-spin"></i></span>
        <span class="is-size-7 has-text-grey ml-1">Memuat komentar...</span>
      </div>
    `);
  }

  $.ajax({
    type: 'GET',
    url: `/get_comments?post_id_give=${post_id}`,
    data: {},
    success: function (response) {
      $list.attr('data-loaded', 'true');
      if (response['result'] === 'success') {
        let comments = response['comments'] || [];
        if (comments.length === 0) {
          $list.html('<p class="has-text-grey-light is-size-7 has-text-centered py-2 empty-comment-msg">Belum ada komentar.</p>');
        } else {
          let tree = build_comment_tree(comments);
          let rendered_html = render_comment_nodes(tree, post_id, 0);
          $list.html(rendered_html);
        }
      } else {
        $list.html(`<div class="notification is-danger is-light is-size-7 p-2 mb-2">${escapeHtml(response['msg'] || 'Gagal memuat komentar.')}</div>`);
      }
    },
    error: function (xhr) {
      if (xhr.status === 401) {
        alert('Sesi Anda telah berakhir. Silakan login kembali.');
        window.location.href = '/login';
        return;
      }
      $list.html(`
        <div class="notification is-danger is-light is-size-7 p-2 mb-2">
          Gagal memuat komentar. <a class="has-text-weight-bold" onclick="load_comments('${post_id}', true)">Coba lagi</a>
        </div>
      `);
    }
  });
}

// --- Tree Hierarchy Construction & Rendering ---

function build_comment_tree(comments) {
  const map = {};
  const roots = [];

  // Inisialisasi setiap node dalam map
  comments.forEach(c => {
    map[c._id] = { ...c, children: [] };
  });

  // Hubungkan child ke parent
  comments.forEach(c => {
    const node = map[c._id];
    if (c.parent_comment_id && map[c.parent_comment_id] && c.parent_comment_id !== c._id) {
      node.parentUsername = map[c.parent_comment_id].username;
      map[c.parent_comment_id].children.push(node);
    } else {
      // Root comment atau fallback jika parent tidak ditemukan
      roots.push(node);
    }
  });

  return roots;
}

function render_comment_nodes(nodes, post_id, depth) {
  let html = '';
  nodes.forEach(node => {
    let time_str = time2str(new Date(node.created_at));
    let is_deleted = Boolean(node.is_deleted);
    let is_mine = Boolean(node.is_mine);
    let avatar_src = sanitizeAvatarUrl(node.profile_pic_real);

    // Batasi indentasi visual maksimal depth 2 agar layout mobile tetap proporsional
    let depth_class = depth === 0 ? 'comment-root' : (depth === 1 ? 'comment-reply-depth-1' : 'comment-reply-depth-2');

    let reply_context_html = '';
    if (node.parent_comment_id && node.parentUsername) {
      reply_context_html = `<span class="reply-context has-text-grey is-size-7"><i class="fa fa-reply fa-flip-horizontal mr-1"></i>membalas <strong>@${escapeHtml(node.parentUsername)}</strong></span><br/>`;
    }

    let content_html = '';
    if (is_deleted) {
      content_html = `<span class="comment-deleted-text has-text-grey-light is-italic is-size-7">[Komentar ini telah dihapus oleh pengguna]</span>`;
    } else {
      content_html = `<span class="comment-content-text">${escapeHtml(node.content)}</span>`;
    }

    let actions_html = '';
    if (!is_deleted) {
      actions_html += `
        <a class="level-item is-sparta btn-action-reply is-size-7" data-username="${escapeHtml(node.username)}" onclick="open_reply_form(this, '${post_id}', '${node._id}')">
          <span class="icon is-small"><i class="fa fa-reply"></i></span>&nbsp;<span>Reply</span>
        </a>
      `;
      if (is_mine) {
        actions_html += `
          <a class="level-item has-text-danger btn-action-delete is-size-7" onclick="delete_comment('${post_id}', '${node._id}')">
            <span class="icon is-small"><i class="fa fa-trash"></i></span>&nbsp;<span>Delete</span>
          </a>
        `;
      }
    }

    let children_html = '';
    if (node.children && node.children.length > 0) {
      children_html = `<div class="comment-children">${render_comment_nodes(node.children, post_id, depth + 1)}</div>`;
    }

    let isStitch = $('body').hasClass('bg-background');

    if (isStitch) {
      let stitch_actions = '';
      if (!is_deleted) {
        stitch_actions += `
          <button class="hover:text-primary transition-colors font-medium cursor-pointer border-0 bg-transparent p-0 text-inherit flex items-center gap-1 btn-action-reply text-[12px]" data-username="${escapeHtml(node.username)}" onclick="open_reply_form(this, '${post_id}', '${node._id}')">
            <i class="fa fa-reply text-[10px]"></i><span>Reply</span>
          </button>
        `;
        if (is_mine) {
          stitch_actions += `
            <button class="text-error hover:underline transition-colors font-medium cursor-pointer border-0 bg-transparent p-0 flex items-center gap-1 btn-action-delete text-[12px] ml-3" onclick="delete_comment('${post_id}', '${node._id}')">
              <i class="fa fa-trash text-[10px]"></i><span>Delete</span>
            </button>
          `;
        }
      }

      html += `
      <div class="flex items-start gap-space-sm comment-item ${depth_class} pt-2 mt-2" id="comment-${node._id}">
        <a href="/user/${encodeURIComponent(node.username)}" class="shrink-0">
          <img class="w-8 h-8 rounded-full object-cover shrink-0 comment-avatar" src="${avatar_src}" alt="${escapeHtml(node.profile_name)}" onerror="this.src='/static/profile_pics/profile_placeholder.png'"/>
        </a>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-space-xs flex-wrap">
            <a href="/user/${encodeURIComponent(node.username)}" class="font-label-sm text-label-sm font-semibold text-on-surface hover:underline no-underline text-inherit">${escapeHtml(node.profile_name)}</a>
            <span class="font-caption text-caption text-on-surface-variant">@${escapeHtml(node.username)} · ${time_str}</span>
          </div>
          ${reply_context_html ? `<div class="font-caption text-caption text-on-surface-variant mt-0.5">${reply_context_html}</div>` : ''}
          <div class="font-body-sm text-body-sm text-on-surface mt-0.5 leading-relaxed comment-content-box">
            ${content_html}
          </div>
          ${stitch_actions ? `<div class="flex items-center gap-space-md mt-1 text-on-surface-variant font-label-sm text-label-sm comment-actions">${stitch_actions}</div>` : ''}
          <div class="reply-form-container" id="reply-form-${node._id}"></div>
          ${children_html}
        </div>
      </div>
      `;
    } else {
      html += `
      <article class="media comment-item ${depth_class}" id="comment-${node._id}">
        <figure class="media-left">
          <a class="image is-24x24" href="/user/${encodeURIComponent(node.username)}">
            <img class="is-rounded comment-avatar" src="${avatar_src}" alt="${escapeHtml(node.profile_name)}">
          </a>
        </figure>
        <div class="media-content">
          <div class="content comment-content-box mb-1">
            <p class="mb-1">
              <strong class="is-size-7">${escapeHtml(node.profile_name)}</strong>
              <small class="is-size-7 has-text-grey">@${escapeHtml(node.username)}</small>
              <small class="is-size-7 has-text-grey-light ml-1">${time_str}</small>
              <br/>
              ${reply_context_html}
              ${content_html}
            </p>
          </div>
          ${actions_html ? `<nav class="level is-mobile comment-actions mb-1"><div class="level-left">${actions_html}</div></nav>` : ''}
          <div class="reply-form-container" id="reply-form-${node._id}"></div>
          ${children_html}
        </div>
      </article>
      `;
    }
  });
  return html;
}

// --- Reply Form Handling ---

function open_reply_form(buttonElement, post_id, comment_id) {
  let $container = $(`#reply-form-${comment_id}`);
  if ($container.children().length > 0) {
    $container.empty();
    return;
  }
  // Tutup form reply lain pada postingan yang sama agar tidak menumpuk
  $(`#comments-section-${post_id} .reply-form-container`).empty();

  let targetUsername = $(buttonElement).data('username') || '';

  let isStitch = $('body').hasClass('bg-background');
  let form_html = '';

  if (isStitch) {
    form_html = `
    <div class="p-3 my-2 bg-surface-container-lowest rounded-lg border border-outline-variant/40 reply-box shadow-sm">
      <div class="flex justify-between items-center mb-1">
        <span class="text-on-surface-variant font-caption text-caption">Membalas <strong class="text-primary font-semibold">@${escapeHtml(targetUsername)}</strong></span>
        <button class="text-on-surface-variant hover:text-on-surface text-caption font-bold border-0 bg-transparent cursor-pointer p-0" onclick="close_reply_form('${comment_id}')" title="Batal">✕</button>
      </div>
      <div class="mb-2">
        <input class="w-full bg-surface-container-low rounded-lg p-2 font-body-sm text-body-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary-container reply-input" id="input-reply-${comment_id}" type="text" placeholder="Tulis balasan Anda..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_reply('${post_id}', '${comment_id}');}" />
      </div>
      <div class="flex justify-end gap-2">
        <button class="px-3 py-1 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm hover:bg-surface-container-high transition-all border-0 cursor-pointer" onclick="close_reply_form('${comment_id}')">Batal</button>
        <button class="px-3 py-1 rounded-full bg-primary-container text-on-primary font-label-sm text-label-sm hover:bg-primary shadow-sm transition-all btn-submit-reply border-0 cursor-pointer flex items-center gap-1" onclick="submit_reply('${post_id}', '${comment_id}')">
          <i class="fa fa-paper-plane text-[10px]"></i><span>Kirim</span>
        </button>
      </div>
    </div>
    `;
  } else {
    form_html = `
    <div class="box is-shadowless p-2 my-2 reply-box">
      <div class="is-flex is-justify-content-space-between is-align-items-center mb-1">
        <small class="has-text-grey is-size-7">Membalas <strong class="is-sparta">@${escapeHtml(targetUsername)}</strong></small>
        <button class="delete is-small" onclick="close_reply_form('${comment_id}')" title="Batal"></button>
      </div>
      <div class="field mb-2">
        <div class="control">
          <input class="input is-small is-rounded reply-input" id="input-reply-${comment_id}" type="text" placeholder="Tulis balasan Anda..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_reply('${post_id}', '${comment_id}');}" />
        </div>
      </div>
      <div class="field is-grouped is-grouped-right mb-0">
        <div class="control">
          <button class="button is-small is-light is-rounded" onclick="close_reply_form('${comment_id}')">Batal</button>
        </div>
        <div class="control">
          <button class="button is-small is-sparta is-rounded btn-submit-reply" onclick="submit_reply('${post_id}', '${comment_id}')">
            <span class="icon is-small"><i class="fa fa-paper-plane"></i></span>
            <span>Kirim</span>
          </button>
        </div>
      </div>
    </div>
    `;
  }

  $container.html(form_html);
  $(`#input-reply-${comment_id}`).focus();
}

function close_reply_form(comment_id) {
  $(`#reply-form-${comment_id}`).empty();
}

// --- Submit Comment & Reply ---

function submit_comment(post_id) {
  let $input = $(`#input-comment-${post_id}`);
  let content = $input.val().trim();
  let $btn = $(`#comments-section-${post_id} .btn-submit-comment`);

  if (!content) {
    alert('Komentar tidak boleh kosong!');
    $input.focus();
    return;
  }
  if (content.length > 2000) {
    alert('Panjang komentar maksimal 2.000 karakter!');
    return;
  }

  if ($btn.hasClass('is-loading')) return;
  $btn.addClass('is-loading').prop('disabled', true);

  $.ajax({
    type: 'POST',
    url: '/create_comment',
    data: {
      post_id_give: post_id,
      content_give: content,
      parent_comment_id_give: ''
    },
    success: function (response) {
      if (response['result'] === 'success') {
        $input.val('');
        update_comment_counter(post_id, 1);
        load_comments(post_id, false);
      } else {
        alert(response['msg'] || 'Gagal mengirim komentar.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal mengirim komentar.');
    },
    complete: function () {
      $btn.removeClass('is-loading').prop('disabled', false);
    }
  });
}

function submit_reply(post_id, parent_comment_id) {
  let $input = $(`#input-reply-${parent_comment_id}`);
  let content = $input.val().trim();
  let $btn = $(`#reply-form-${parent_comment_id} .btn-submit-reply`);

  if (!content) {
    alert('Balasan tidak boleh kosong!');
    $input.focus();
    return;
  }
  if (content.length > 2000) {
    alert('Panjang balasan maksimal 2.000 karakter!');
    return;
  }

  if ($btn.hasClass('is-loading')) return;
  $btn.addClass('is-loading').prop('disabled', true);

  $.ajax({
    type: 'POST',
    url: '/create_comment',
    data: {
      post_id_give: post_id,
      content_give: content,
      parent_comment_id_give: parent_comment_id
    },
    success: function (response) {
      if (response['result'] === 'success') {
        close_reply_form(parent_comment_id);
        update_comment_counter(post_id, 1);
        load_comments(post_id, false);
      } else {
        alert(response['msg'] || 'Gagal mengirim balasan.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal mengirim balasan.');
    },
    complete: function () {
      $btn.removeClass('is-loading').prop('disabled', false);
    }
  });
}

// --- Delete Comment ---

function delete_comment(post_id, comment_id) {
  if (!confirm('Apakah Anda yakin ingin menghapus komentar ini?')) {
    return;
  }

  $.ajax({
    type: 'POST',
    url: '/delete_comment',
    data: {
      comment_id_give: comment_id
    },
    success: function (response) {
      if (response['result'] === 'success') {
        // Refresh list komentar (tanpa loading spinner)
        // Counter TIDAK dikurangi karena backend soft delete tetap menghitungnya
        load_comments(post_id, false);
      } else {
        alert(response['msg'] || 'Gagal menghapus komentar.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal menghapus komentar.');
    }
  });
}

// --- Comment Counter Update ---

function update_comment_counter(post_id, delta) {
  let $counter = $(`#${post_id} .comment-num`);
  if ($counter.length > 0) {
    let current = parseInt($counter.attr('data-count') || '0', 10);
    let updated = Math.max(0, current + delta);
    $counter.attr('data-count', updated);
    $counter.text(updated > 0 ? num2str(updated) : '0');
  }
}
