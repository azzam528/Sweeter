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

// ==========================================
// FEED & TIMELINE CONTROLLERS (SWEETER V2)
// ==========================================

window.currentFeedTab = 'following';
window.feedNextCursor = null;
window.feedHasMore = false;
window.feedLoading = false;
window.currentProfileUsername = '';

function switch_feed_tab(targetTab) {
  if (targetTab !== 'following' && targetTab !== 'explore') {
    targetTab = 'following';
  }
  window.currentFeedTab = targetTab;
  window.feedNextCursor = null;
  window.feedHasMore = false;

  $('.feed-tab').removeClass('text-primary font-bold').addClass('text-on-surface-variant font-medium');
  $('.feed-tab .tab-indicator').addClass('hidden');

  let $activeBtn = $(`#tab-${targetTab}`);
  $activeBtn.removeClass('text-on-surface-variant font-medium').addClass('text-primary font-bold');
  $activeBtn.find('.tab-indicator').removeClass('hidden');

  get_posts('', targetTab, '', false);
}

function load_more_posts() {
  if (window.isNotificationsPage) {
    if (window.notifsLoading || !window.notifsHasMore || !window.notifsNextCursor) return;
    load_notifications(window.notifsNextCursor, true);
    return;
  }
  if (window.isBookmarksPage) {
    if (window.bookmarksLoading || !window.bookmarksHasMore || !window.bookmarksNextCursor) return;
    load_bookmarks(window.bookmarksNextCursor, true);
    return;
  }
  if (window.feedLoading || !window.feedHasMore || !window.feedNextCursor) return;
  get_posts(window.currentProfileUsername || '', window.currentFeedTab || 'following', window.feedNextCursor, true);
}

// --- Post Card Renderer (Stitch & Bulma) ---

function render_post_card(post, isStitch) {
  window.postsCache = window.postsCache || {};
  window.postsCache[post['_id']] = post;
  if (post['original_post'] && post['original_post']['_id']) {
    window.postsCache[post['original_post']['_id']] = post['original_post'];
  }

  let post_type = post['post_type'] || 'original';
  let orig = post['original_post'];

  let time_post = new Date(post['date']);
  let time_before = time2str(time_post);

  let avatar_url = sanitizeAvatarUrl(post['profile_pic_real']);
  let profile_name_escaped = escapeHtml(post['profile_name']);
  let username_escaped = escapeHtml(post['username']);
  let comment_escaped = escapeHtml(post['comment']);

  let is_edited = Boolean(post['is_edited']);
  let is_mine = Boolean(post['is_mine']);
  let is_bookmarked = Boolean(post['is_bookmarked']);

  let class_heart = post['heart_by_me'] ? 'fa-heart' : 'fa-heart-o';
  let class_stars = post['stars_by_me'] ? 'fa-star' : 'fa-star-o';
  let class_thumbs = post['thumbs_by_me'] ? 'fa-thumbs-up' : 'fa-thumbs-o-up';

  let count_heart = post['count_heart'] || 0;
  let count_stars = post['count_stars'] || 0;
  let count_thumbs = post['count_thumbs'] || 0;
  let count_repost = post['count_repost'] || 0;
  let repost_by_me = Boolean(post['repost_by_me']);

  let count_comments = (post['count_comments'] !== undefined && post['count_comments'] !== null)
    ? post['count_comments']
    : 0;
  let display_comment_num = count_comments > 0 ? num2str(count_comments) : '0';

  let target_post_id = post['_id'];
  let repost_banner_stitch = '';
  let repost_banner_bulma = '';
  let quote_embed_stitch = '';
  let quote_embed_bulma = '';

  if (post_type === 'repost') {
    repost_banner_stitch = `
      <div class="flex items-center gap-1.5 text-on-surface-variant font-label-sm text-label-sm mb-2 pb-1 border-b border-surface-container-high/40">
        <i class="fa fa-retweet text-primary text-[14px]"></i>
        <a href="/user/${encodeURIComponent(post['username'])}" class="font-semibold text-on-surface hover:underline no-underline text-inherit">${profile_name_escaped}</a>
        <span>merepost</span>
      </div>
    `;
    repost_banner_bulma = `
      <div class="is-flex is-align-items-center mb-2 pb-1" style="border-bottom: 1px solid #f0f0f0; color: #7a7a7a; font-size: 0.8rem;">
        <span class="icon is-small mr-1"><i class="fa fa-retweet has-text-primary"></i></span>
        <a href="/user/${encodeURIComponent(post['username'])}" class="has-text-weight-semibold mr-1">${profile_name_escaped}</a>
        <span>merepost</span>
      </div>
    `;

    if (orig && !orig.is_deleted) {
      target_post_id = orig['_id'];
      avatar_url = sanitizeAvatarUrl(orig['profile_pic_real']);
      profile_name_escaped = escapeHtml(orig['profile_name']);
      username_escaped = escapeHtml(orig['username']);
      comment_escaped = escapeHtml(orig['comment']);
      time_before = time2str(new Date(orig['date']));
      is_edited = Boolean(orig['is_edited']);

      class_heart = orig['heart_by_me'] ? 'fa-heart' : 'fa-heart-o';
      count_heart = orig['count_heart'] || 0;
      class_stars = orig['stars_by_me'] ? 'fa-star' : 'fa-star-o';
      count_stars = orig['count_stars'] || 0;
      class_thumbs = orig['thumbs_by_me'] ? 'fa-thumbs-up' : 'fa-thumbs-o-up';
      count_thumbs = orig['count_thumbs'] || 0;
      count_comments = (orig['count_comments'] !== undefined && orig['count_comments'] !== null) ? orig['count_comments'] : 0;
      display_comment_num = count_comments > 0 ? num2str(count_comments) : '0';
      count_repost = orig['count_repost'] || 0;
      repost_by_me = Boolean(orig['repost_by_me']);
      is_bookmarked = Boolean(orig['is_bookmarked']);
    } else {
      comment_escaped = '<span class="italic text-on-surface-variant font-body-sm">Postingan asli telah dihapus atau tidak tersedia.</span>';
    }
  } else if (post_type === 'quote') {
    target_post_id = post['_id'];
    if (!orig || orig.is_deleted) {
      quote_embed_stitch = `
        <div class="mt-2 mb-space-md p-3.5 rounded-xl border border-outline-variant/50 bg-surface-container-low text-on-surface-variant font-body-sm italic flex items-center gap-2">
          <span class="material-symbols-outlined text-[18px] text-outline">info</span>
          <span>Postingan asli telah dihapus atau tidak tersedia.</span>
        </div>
      `;
      quote_embed_bulma = `
        <div class="notification is-light p-3 mt-2 mb-3" style="border: 1px solid #dbdbdb; border-radius: 8px;">
          <span class="icon is-small mr-1"><i class="fa fa-info-circle"></i></span>
          <span class="is-italic is-size-7">Postingan asli telah dihapus atau tidak tersedia.</span>
        </div>
      `;
    } else {
      quote_embed_stitch = `
        <div class="mt-2 mb-space-md p-3.5 rounded-xl border border-outline-variant/60 bg-surface-container-low/60 hover:bg-surface-container-low transition-colors cursor-pointer" onclick="window.location.href='/user/${encodeURIComponent(orig.username)}#${orig._id}'">
          <div class="flex items-center gap-2 mb-1.5">
            <img src="${sanitizeAvatarUrl(orig.profile_pic_real)}" alt="${escapeHtml(orig.profile_name)}" class="w-5 h-5 rounded-full object-cover shrink-0" onerror="this.src='/static/profile_pics/profile_placeholder.png'"/>
            <span class="font-label-sm font-semibold text-on-surface">${escapeHtml(orig.profile_name)}</span>
            <span class="font-caption text-on-surface-variant">@${escapeHtml(orig.username)}</span>
            <span class="font-caption text-on-surface-variant">·</span>
            <span class="font-caption text-on-surface-variant">${time2str(new Date(orig.date))}</span>
          </div>
          <p class="font-body-sm text-on-surface whitespace-pre-wrap leading-relaxed m-0 line-clamp-4">${escapeHtml(orig.comment)}</p>
        </div>
      `;
      quote_embed_bulma = `
        <div class="box is-light p-3 mt-2 mb-3" style="border: 1px solid #dbdbdb; border-radius: 8px; cursor: pointer;" onclick="window.location.href='/user/${encodeURIComponent(orig.username)}#${orig._id}'">
          <div class="is-flex is-align-items-center mb-1">
            <figure class="image is-20x20 mr-2">
              <img class="is-rounded" src="${sanitizeAvatarUrl(orig.profile_pic_real)}" onerror="this.src='/static/profile_pics/profile_placeholder.png'">
            </figure>
            <strong class="is-size-7">${escapeHtml(orig.profile_name)}</strong>
            <small class="has-text-grey ml-1 is-size-7">@${escapeHtml(orig.username)} · ${time2str(new Date(orig.date))}</small>
          </div>
          <p class="is-size-7">${escapeHtml(orig.comment)}</p>
        </div>
      `;
    }
  }

  if (isStitch) {
    let edited_badge = is_edited
      ? `<span class="text-on-surface-variant font-caption text-caption italic ml-1 post-edited-indicator" id="edited-badge-${post['_id']}">· edited</span>`
      : `<span class="text-on-surface-variant font-caption text-caption italic ml-1 post-edited-indicator hidden" id="edited-badge-${post['_id']}">· edited</span>`;

    let owner_actions = '';
    if (post_type === 'repost') {
      if (is_mine) {
        owner_actions = `
        <div class="flex items-center gap-space-xs text-on-surface-variant">
          <button type="button" onclick="delete_post('${post['_id']}')" class="p-1 rounded-full hover:bg-error-container hover:text-error transition-colors text-on-surface-variant border-0 bg-transparent cursor-pointer" title="Hapus Repost">
            <span class="material-symbols-outlined text-[18px]">delete</span>
          </button>
        </div>`;
      }
    } else {
      if (is_mine) {
        owner_actions = `
        <div class="flex items-center gap-space-xs text-on-surface-variant">
          <button type="button" onclick="open_edit_post('${post['_id']}')" class="p-1 rounded-full hover:bg-surface-container-high transition-colors text-on-surface-variant hover:text-on-surface border-0 bg-transparent cursor-pointer" title="Edit Post">
            <span class="material-symbols-outlined text-[18px]">edit</span>
          </button>
          <button type="button" onclick="delete_post('${post['_id']}')" class="p-1 rounded-full hover:bg-error-container hover:text-error transition-colors text-on-surface-variant border-0 bg-transparent cursor-pointer" title="Hapus Post">
            <span class="material-symbols-outlined text-[18px]">delete</span>
          </button>
        </div>`;
      }
    }

    return `
    <article class="bg-surface-container-lowest rounded-xl p-space-md shadow-sm transition-all hover:shadow-md mb-space-md" id="${post['_id']}">
      ${repost_banner_stitch}
      <!-- Post Header -->
      <div class="flex items-center justify-between mb-space-sm">
        <div class="flex items-center gap-space-sm min-w-0">
          <a href="/user/${encodeURIComponent(post_type === 'repost' && orig ? orig.username : post['username'])}" class="shrink-0">
            <img class="w-10 h-10 rounded-full object-cover shrink-0" src="${avatar_url}" alt="${profile_name_escaped}" onerror="this.src='/static/profile_pics/profile_placeholder.png'"/>
          </a>
          <div class="flex flex-col min-w-0">
            <div class="flex items-center gap-space-xs">
              <a href="/user/${encodeURIComponent(post_type === 'repost' && orig ? orig.username : post['username'])}" class="font-label-md text-label-md text-on-surface font-semibold truncate hover:underline no-underline text-inherit">${profile_name_escaped}</a>
            </div>
            <div class="flex items-center gap-space-xs text-on-surface-variant font-caption text-caption">
              <span>@${username_escaped}</span>
              <span>·</span>
              <span>${time_before}</span>
              ${edited_badge}
            </div>
          </div>
        </div>
        ${owner_actions}
      </div>

      <!-- Post Body -->
      <p class="font-body-md text-body-md text-on-surface mb-space-md leading-relaxed whitespace-pre-wrap" id="post-text-${post['_id']}">
        ${comment_escaped}
      </p>

      ${quote_embed_stitch}

      <!-- Inline Edit Box -->
      ${post_type !== 'repost' ? `
      <div id="edit-box-${post['_id']}" class="hidden mb-space-md bg-surface-container-low p-space-sm rounded-lg">
        <textarea id="edit-textarea-${post['_id']}" class="w-full bg-surface-container-lowest rounded-lg p-space-sm font-body-md text-body-md text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary-container resize-none transition-all" rows="3" maxlength="5000">${comment_escaped}</textarea>
        <div class="flex items-center justify-end gap-space-xs mt-space-xs">
          <button type="button" onclick="cancel_edit_post('${post['_id']}')" class="px-space-sm py-1 font-label-sm text-label-sm text-on-surface-variant hover:bg-surface-container-high rounded-full border-0 bg-transparent cursor-pointer">Batal</button>
          <button type="button" onclick="submit_edit_post('${post['_id']}')" class="px-space-md py-1 bg-primary-container text-on-primary font-label-sm text-label-sm rounded-full hover:bg-primary transition-colors border-0 cursor-pointer btn-save-edit">Simpan</button>
        </div>
      </div>` : ''}

      <!-- Action Counter Toolbar -->
      <div class="flex items-center justify-between text-on-surface-variant font-label-sm text-label-sm mb-space-md pt-space-xs">
        <div class="flex items-center gap-space-md">
          <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${class_heart === 'fa-heart' ? 'text-primary font-bold' : ''}" aria-label="heart" onclick="toggle_like('${target_post_id}', 'heart')">
            <span class="icon is-small">
              <i class="fa ${class_heart}" aria-hidden="true"></i>
            </span>&nbsp;<span class="like-num">${count_heart}</span>
          </a>
          <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${class_stars === 'fa-star' ? 'text-primary font-bold' : ''}" aria-label="stars" onclick="toggle_stars('${target_post_id}', 'stars')">
            <span class="icon is-small">
              <i class="fa ${class_stars}" aria-hidden="true"></i>
            </span>&nbsp;<span class="star-num">${num2str(count_stars)}</span>
          </a>
          <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline ${class_thumbs === 'fa-thumbs-up' ? 'text-primary font-bold' : ''}" aria-label="thumbs" onclick="toggle_thumbs('${target_post_id}', 'thumbs')">
            <span class="icon is-small">
              <i class="fa ${class_thumbs}" aria-hidden="true"></i>
            </span>&nbsp;<span class="thumbs-num">${num2str(count_thumbs)}</span>
          </a>

          <!-- Repost Menu Dropdown Button -->
          <div class="relative inline-block repost-menu-container">
            <button type="button" class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline btn-repost border-0 bg-transparent p-0 ${repost_by_me ? 'text-primary font-bold' : ''}" aria-label="repost" onclick="toggle_repost_dropdown('${target_post_id}', event)" title="Repost / Kutip">
              <span class="icon is-small">
                <i class="fa fa-retweet ${repost_by_me ? 'text-primary' : ''}" id="repost-icon-${target_post_id}" aria-hidden="true"></i>
              </span>&nbsp;<span class="repost-num" id="repost-num-${target_post_id}">${num2str(count_repost)}</span>
            </button>
            <div id="repost-dropdown-${target_post_id}" class="hidden absolute left-0 bottom-full mb-1 z-30 bg-surface-container-lowest border border-outline-variant/30 rounded-xl shadow-lg py-1.5 w-44 text-left font-label-md text-label-md">
              <button type="button" class="w-full text-left px-3.5 py-2 hover:bg-surface-container-high transition-colors flex items-center gap-2 border-0 bg-transparent cursor-pointer text-on-surface" onclick="toggle_repost('${target_post_id}'); hide_all_repost_dropdowns();">
                <i class="fa fa-retweet ${repost_by_me ? 'text-primary' : ''}"></i>
                <span>${repost_by_me ? 'Batalkan Repost' : 'Repost'}</span>
              </button>
              <button type="button" class="w-full text-left px-3.5 py-2 hover:bg-surface-container-high transition-colors flex items-center gap-2 border-0 bg-transparent cursor-pointer text-on-surface" onclick="open_quote_modal('${target_post_id}'); hide_all_repost_dropdowns();">
                <i class="fa fa-quote-left"></i>
                <span>Kutip Postingan</span>
              </button>
            </div>
          </div>

          <!-- Comments Button -->
          <a class="flex items-center gap-1 text-primary-container font-semibold btn-toggle-comment cursor-pointer text-inherit no-underline" aria-label="comment" onclick="toggle_comments('${target_post_id}')" title="Komentar">
            <span class="icon is-small">
              <i class="fa fa-comment-o" aria-hidden="true"></i>
            </span>&nbsp;<span class="comment-num" data-count="${count_comments}">${display_comment_num}</span>
          </a>
        </div>

        <!-- Bookmark Button -->
        <a class="flex items-center gap-1 group hover:text-primary transition-colors cursor-pointer text-inherit no-underline btn-bookmark ${is_bookmarked ? 'text-primary font-bold' : ''}" aria-label="bookmark" onclick="toggle_bookmark('${target_post_id}')" title="Bookmark">
          <span class="icon is-small">
            <i class="fa ${is_bookmarked ? 'fa-bookmark text-primary' : 'fa-bookmark-o'}" id="bookmark-icon-${target_post_id}" aria-hidden="true"></i>
          </span>
        </a>
      </div>

      <!-- Container Komentar (Collapsible) -->
      <div class="comment-section is-hidden bg-surface-container-low rounded-lg p-space-md mt-space-sm" id="comments-section-${target_post_id}">
        <!-- Form Input Komentar Utama -->
        <div class="comment-form mb-3">
          <div class="flex items-center gap-space-sm">
            <div class="flex-1 relative flex items-center">
              <input class="w-full bg-surface-container-lowest rounded-full py-space-xs pl-space-md pr-20 font-body-sm text-body-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-1 focus:ring-primary-container comment-input" id="input-comment-${target_post_id}" type="text" placeholder="Tulis komentar..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_comment('${target_post_id}');}" />
              <button class="absolute right-1 px-space-sm py-1 bg-primary-container text-on-primary font-label-sm text-label-sm rounded-full hover:bg-primary transition-colors btn-submit-comment border-0 cursor-pointer" onclick="submit_comment('${target_post_id}')">
                Reply
              </button>
            </div>
          </div>
        </div>

        <!-- Container List Komentar (Tree) -->
        <div class="comment-list" id="comments-list-${target_post_id}"></div>
      </div>
    </article>
    `;
  } else {
    let edited_badge_bulma = is_edited
      ? `<small class="has-text-grey-light is-italic ml-1 post-edited-indicator" id="edited-badge-${post['_id']}">· edited</small>`
      : `<small class="has-text-grey-light is-italic ml-1 post-edited-indicator is-hidden" id="edited-badge-${post['_id']}">· edited</small>`;

    let owner_actions_bulma = '';
    if (post_type === 'repost') {
      if (is_mine) {
        owner_actions_bulma = `
        <div class="media-right">
          <button type="button" class="button is-small is-white has-text-danger" onclick="delete_post('${post['_id']}')" title="Hapus Repost"><i class="fa fa-trash"></i></button>
        </div>`;
      }
    } else {
      if (is_mine) {
        owner_actions_bulma = `
        <div class="media-right">
          <button type="button" class="button is-small is-white" onclick="open_edit_post('${post['_id']}')" title="Edit"><i class="fa fa-pencil"></i></button>
          <button type="button" class="button is-small is-white has-text-danger" onclick="delete_post('${post['_id']}')" title="Hapus"><i class="fa fa-trash"></i></button>
        </div>`;
      }
    }

    return `
    <div class="box" id="${post['_id']}">
      ${repost_banner_bulma}
      <article class="media">
        <div class="media-left">
          <a class="image is-64x64" href="/user/${encodeURIComponent(post_type === 'repost' && orig ? orig.username : post['username'])}">
            <img class="is-rounded" src="${avatar_url}" alt="${profile_name_escaped}"/>
          </a>
        </div>
        <div class="media-content">
          <div class="content">
            <p id="post-text-${post['_id']}">
              <strong>${profile_name_escaped}</strong> <small>@${username_escaped}</small>
              <small>${time_before}</small>
              ${edited_badge_bulma}
              <br/>
              ${comment_escaped}
            </p>
            ${quote_embed_bulma}
            ${post_type !== 'repost' ? `
            <div id="edit-box-${post['_id']}" class="is-hidden mb-3">
              <textarea id="edit-textarea-${post['_id']}" class="textarea is-small" rows="3" maxlength="5000">${comment_escaped}</textarea>
              <div class="buttons is-right mt-1">
                <button type="button" class="button is-small is-light" onclick="cancel_edit_post('${post['_id']}')">Batal</button>
                <button type="button" class="button is-small is-sparta btn-save-edit" onclick="submit_edit_post('${post['_id']}')">Simpan</button>
              </div>
            </div>` : ''}
          </div>
          <nav class="level is-mobile mb-2">
            <div class="level-left"> 
              <a class="level-item is-sparta" aria-label="heart" onclick="toggle_like('${target_post_id}', 'heart')">
                <span class="icon is-small">
                  <i class="fa ${class_heart}" aria-hidden="true"></i>
                </span>&nbsp;<span class="like-num">${count_heart}</span>
              </a>
              <a class="level-item is-sparta" aria-label="stars" onclick="toggle_stars('${target_post_id}', 'stars')">
                <span class="icon is-small">
                  <i class="fa ${class_stars}" aria-hidden="true"></i>
                </span>&nbsp;<span class="star-num">${num2str(count_stars)}</span>
              </a>
              <a class="level-item is-sparta" aria-label="thumbs" onclick="toggle_thumbs('${target_post_id}', 'thumbs')">
                <span class="icon is-small">
                  <i class="fa ${class_thumbs}" aria-hidden="true"></i>
                </span>&nbsp;<span class="thumbs-num">${num2str(count_thumbs)}</span>
              </a>

              <!-- Bulma Repost Menu Dropdown Button -->
              <div class="level-item is-sparta repost-menu-container" style="position: relative;">
                <a class="is-sparta btn-repost ${repost_by_me ? 'has-text-primary has-text-weight-bold' : ''}" aria-label="repost" onclick="toggle_repost_dropdown('${target_post_id}', event)" title="Repost / Kutip">
                  <span class="icon is-small"><i class="fa fa-retweet ${repost_by_me ? 'has-text-primary' : ''}" id="repost-icon-bulma-${target_post_id}"></i></span>&nbsp;<span class="repost-num" id="repost-num-bulma-${target_post_id}">${num2str(count_repost)}</span>
                </a>
                <div id="repost-dropdown-${target_post_id}" class="is-hidden" style="position: absolute; bottom: 100%; left: 0; background: white; border: 1px solid #dbdbdb; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); z-index: 30; min-width: 160px; padding: 4px 0;">
                  <a class="dropdown-item is-size-7" onclick="toggle_repost('${target_post_id}'); hide_all_repost_dropdowns();" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; color: #363636;">
                    <i class="fa fa-retweet ${repost_by_me ? 'has-text-primary' : ''}"></i>
                    <span>${repost_by_me ? 'Batalkan Repost' : 'Repost'}</span>
                  </a>
                  <a class="dropdown-item is-size-7" onclick="open_quote_modal('${target_post_id}'); hide_all_repost_dropdowns();" style="display: flex; align-items: center; gap: 6px; padding: 6px 12px; color: #363636;">
                    <i class="fa fa-quote-left"></i>
                    <span>Kutip Postingan</span>
                  </a>
                </div>
              </div>

              <!-- Comments Button -->
              <a class="level-item is-sparta btn-toggle-comment" aria-label="comment" onclick="toggle_comments('${target_post_id}')" title="Komentar">
                <span class="icon is-small">
                  <i class="fa fa-comment-o" aria-hidden="true"></i>
                </span>&nbsp;<span class="comment-num" data-count="${count_comments}">${display_comment_num}</span>
              </a>
            </div>
            <div class="level-right">
              <a class="level-item is-sparta btn-bookmark ${is_bookmarked ? 'has-text-primary' : ''}" aria-label="bookmark" onclick="toggle_bookmark('${target_post_id}')" title="Bookmark">
                <span class="icon is-small"><i class="fa ${is_bookmarked ? 'fa-bookmark' : 'fa-bookmark-o'}" id="bookmark-icon-${target_post_id}"></i></span>
              </a>
            </div>
          </nav>

          <!-- Container Komentar (Collapsible) -->
          <div class="comment-section is-hidden" id="comments-section-${target_post_id}">
            <hr class="comment-divider"/>
            
            <!-- Form Input Komentar Utama -->
            <div class="comment-form mb-3">
              <div class="field has-addons">
                <div class="control is-expanded">
                  <input class="input is-small is-rounded comment-input" id="input-comment-${target_post_id}" type="text" placeholder="Tulis komentar..." maxlength="2000" onkeydown="if(event.key==='Enter'){submit_comment('${target_post_id}');}" />
                </div>
                <div class="control">
                  <button class="button is-small is-sparta is-rounded btn-submit-comment" onclick="submit_comment('${target_post_id}')">
                    <span class="icon is-small"><i class="fa fa-paper-plane"></i></span>
                    <span>Kirim</span>
                  </button>
                </div>
              </div>
            </div>

            <!-- Container List Komentar (Tree) -->
            <div class="comment-list" id="comments-list-${target_post_id}"></div>
          </div>

        </div>
        ${owner_actions_bulma}
      </article>
    </div>
    `;
  }
}

// --- Pengambilan & Rendering Feed Postingan ---

function get_posts(username, tab, cursor, append = false) {
  if (username === undefined || username === null) {
    username = '';
  }
  if (!tab) {
    tab = window.currentFeedTab || 'following';
  }
  if (!cursor) {
    cursor = '';
  }

  window.currentProfileUsername = username;
  window.feedLoading = true;

  let isStitch = $('body').hasClass('bg-background');

  if (append) {
    $('#feed-loading').removeClass('hidden');
    $('#load-more-btn').prop('disabled', true).html('<i class="fa fa-spinner fa-spin mr-1"></i> Memuat...');
  } else {
    $('#load-more-container').addClass('hidden');
    if (isStitch) {
      $('#post-box').html(`
        <div class="text-center text-on-surface-variant py-8 font-body-sm">
          <i class="fa fa-spinner fa-spin text-xl text-primary mr-2"></i> Memuat postingan…
        </div>
      `);
    } else {
      $('#post-box').html(`
        <div class="has-text-centered py-6">
          <span class="icon"><i class="fa fa-spinner fa-spin"></i></span>
          <p class="has-text-grey is-size-7 mt-1">Memuat postingan...</p>
        </div>
      `);
    }
  }

  let requestUrl = `/get_posts?username_give=${encodeURIComponent(username)}&tab=${encodeURIComponent(tab)}&limit=10`;
  if (cursor) {
    requestUrl += `&cursor=${encodeURIComponent(cursor)}`;
  }

  $.ajax({
    type: 'GET',
    url: requestUrl,
    data: {},
    success: function (response) {
      if (response['result'] === 'success') {
        let posts = response['posts'] || [];

        window.feedNextCursor = response['next_cursor'];
        window.feedHasMore = Boolean(response['has_more']);

        if (!append) {
          $('#post-box').empty();
        }

        if (!posts || posts.length === 0) {
          if (!append) {
            if (isStitch) {
              if (username) {
                // Empty state profil
                $('#post-box').html(`
                  <div class="text-center text-on-surface-variant py-12 bg-surface-container-lowest rounded-xl p-6 shadow-sm">
                    <span class="material-symbols-outlined text-[36px] text-outline block mb-2 mx-auto">article</span>
                    <p class="font-body-md text-body-md text-on-surface font-semibold mb-1">Belum ada postingan</p>
                    <p class="font-caption text-caption text-on-surface-variant">Pengguna ini belum membagikan kiriman apapun.</p>
                  </div>
                `);
              } else if (tab === 'following') {
                // Empty state timeline personal
                $('#post-box').html(`
                  <div class="text-center py-12 px-6 bg-surface-container-lowest rounded-xl shadow-sm my-2">
                    <span class="material-symbols-outlined text-[44px] text-outline-variant mb-2 block mx-auto">group_add</span>
                    <h3 class="font-headline-sm text-[17px] font-bold text-on-surface mb-1">Timeline Anda masih sepi</h3>
                    <p class="font-body-sm text-secondary max-w-sm mx-auto mb-4 leading-relaxed">
                      Ikuti pengguna lain untuk melihat kiriman mereka di sini, atau beralih ke tab Jelajahi untuk melihat kiriman publik.
                    </p>
                    <button type="button" onclick="switch_feed_tab('explore')" class="px-5 py-2 rounded-full bg-primary-container text-on-primary font-label-md text-label-md hover:bg-primary transition-all border-0 cursor-pointer shadow-sm">
                      Jelajahi Postingan
                    </button>
                  </div>
                `);
              } else {
                // Empty state explore
                $('#post-box').html(`
                  <div class="text-center py-12 px-6 bg-surface-container-lowest rounded-xl shadow-sm my-2">
                    <span class="material-symbols-outlined text-[44px] text-outline-variant mb-2 block mx-auto">edit_note</span>
                    <h3 class="font-headline-sm text-[17px] font-bold text-on-surface mb-1">Belum ada postingan publik</h3>
                    <p class="font-body-sm text-secondary max-w-sm mx-auto mb-4 leading-relaxed">
                      Jadilah orang pertama yang membagikan pemikiran Anda di Sweeter!
                    </p>
                    <button type="button" onclick="$('#post-textarea').focus()" class="px-5 py-2 rounded-full bg-primary-container text-on-primary font-label-md text-label-md hover:bg-primary transition-all border-0 cursor-pointer shadow-sm">
                      Tulis Postingan
                    </button>
                  </div>
                `);
              }
            } else {
              $('#post-box').html(`
                <div class="has-text-centered has-text-grey py-6">
                  <p>Belum ada postingan.</p>
                </div>
              `);
            }
          }
          $('#load-more-container').addClass('hidden');
          return;
        }

        for (let i = 0; i < posts.length; i++) {
          $('#post-box').append(render_post_card(posts[i], isStitch));
        }

        if (window.feedHasMore) {
          $('#load-more-container').removeClass('hidden');
          $('#load-more-btn').prop('disabled', false).html('Load More');
        } else {
          $('#load-more-container').addClass('hidden');
        }
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memuat postingan.');
    },
    complete: function () {
      window.feedLoading = false;
      $('#feed-loading').addClass('hidden');
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

// ==========================================
// FOLLOW & UNFOLLOW SYSTEM FUNCTIONS (SWEETER V2)
// ==========================================

function toggle_follow(target_username, btnSelector, countSelector) {
  let $btn = btnSelector ? $(btnSelector) : $('#follow-btn');
  let $count = countSelector ? $(countSelector) : $('#follower-count-display');

  if (!$btn.length || !target_username) return;

  let isFollowing = $btn.attr('data-following') === 'true';
  let endpoint = isFollowing ? '/api/unfollow' : '/api/follow';

  $btn.prop('disabled', true).addClass('opacity-75');

  $.ajax({
    type: 'POST',
    url: endpoint,
    data: {
      target_username_give: target_username
    },
    success: function (res) {
      if (res.result === 'success') {
        let nowFollowing = Boolean(res.is_following);
        $btn.attr('data-following', nowFollowing ? 'true' : 'false');

        if (nowFollowing) {
          $btn.removeClass('bg-primary-container text-on-primary hover:bg-primary border-0')
              .addClass('bg-surface-container-high text-on-surface border border-outline-variant group hover:bg-error-container hover:text-on-error-container')
              .html('<span class="group-hover:hidden">Following</span><span class="hidden group-hover:inline">Unfollow</span>');
        } else {
          $btn.removeClass('bg-surface-container-high text-on-surface border border-outline-variant group hover:bg-error-container hover:text-on-error-container')
              .addClass('bg-primary-container text-on-primary hover:bg-primary border-0')
              .html('Follow');
        }

        if ($count.length && res.follower_count !== undefined) {
          $count.text(res.follower_count);
        }
      } else {
        alert(res.msg || 'Aksi follow/unfollow gagal.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memproses aksi follow/unfollow.');
    },
    complete: function () {
      $btn.prop('disabled', false).removeClass('opacity-75');
    }
  });
}

// ==========================================
// BOOKMARKS & POST MANAGEMENT (SWEETER V2)
// ==========================================

function render_empty_bookmarks() {
  $('#post-box').html(`
    <div class="text-center py-12 px-6 bg-surface-container-lowest rounded-xl shadow-sm my-2" id="bookmarks-empty-state">
      <span class="material-symbols-outlined text-[44px] text-outline-variant mb-2 block mx-auto">bookmark_border</span>
      <h3 class="font-headline-sm text-[17px] font-bold text-on-surface mb-1">Belum ada postingan tersimpan</h3>
      <p class="font-body-sm text-secondary max-w-sm mx-auto mb-4 leading-relaxed">
        Simpan postingan menarik dengan menekan ikon bookmark untuk dibaca kembali nanti.
      </p>
      <a href="/" class="inline-block px-5 py-2 rounded-full bg-primary-container text-on-primary font-label-md text-label-md hover:bg-primary transition-all border-0 cursor-pointer shadow-sm no-underline">
        Jelajahi Beranda
      </a>
    </div>
  `);
}

function load_bookmarks(cursor, append = false) {
  if (window.bookmarksLoading) return;
  window.bookmarksLoading = true;

  let isStitch = $('body').hasClass('bg-background');

  if (append) {
    $('#feed-loading').removeClass('hidden');
    $('#load-more-btn').prop('disabled', true).html('<i class="fa fa-spinner fa-spin mr-1"></i> Memuat...');
  } else {
    $('#load-more-container').addClass('hidden');
    if (isStitch) {
      $('#post-box').html(`
        <div class="text-center text-on-surface-variant py-8 font-body-sm">
          <i class="fa fa-spinner fa-spin text-xl text-primary mr-2"></i> Memuat postingan tersimpan…
        </div>
      `);
    } else {
      $('#post-box').html(`
        <div class="has-text-centered py-6">
          <span class="icon"><i class="fa fa-spinner fa-spin"></i></span>
          <p class="has-text-grey is-size-7 mt-1">Memuat postingan tersimpan...</p>
        </div>
      `);
    }
  }

  let requestUrl = '/api/bookmarks?limit=20';
  if (cursor) {
    requestUrl += `&cursor=${encodeURIComponent(cursor)}`;
  }

  $.ajax({
    type: 'GET',
    url: requestUrl,
    success: function (response) {
      if (response['result'] === 'success') {
        let posts = response['posts'] || [];
        window.bookmarksNextCursor = response['next_cursor'];
        window.bookmarksHasMore = Boolean(response['has_more']);

        if (!append) {
          $('#post-box').empty();
        }

        if (!posts || posts.length === 0) {
          if (!append) {
            render_empty_bookmarks();
          }
          $('#load-more-container').addClass('hidden');
          return;
        }

        for (let i = 0; i < posts.length; i++) {
          $('#post-box').append(render_post_card(posts[i], isStitch));
        }

        if (window.bookmarksHasMore) {
          $('#load-more-container').removeClass('hidden');
          $('#load-more-btn').prop('disabled', false).html('Load More');
        } else {
          $('#load-more-container').addClass('hidden');
        }
      } else {
        handle_ajax_error(null, response['msg'] || 'Gagal memuat bookmark.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memuat postingan tersimpan.');
    },
    complete: function () {
      window.bookmarksLoading = false;
      $('#feed-loading').addClass('hidden');
    }
  });
}

function toggle_bookmark(post_id) {
  let $icon = $(`#bookmark-icon-${post_id}`);
  let $btn = $(`#${post_id} a[aria-label='bookmark']`);
  let isBookmarked = $icon.hasClass('fa-bookmark');
  let endpoint = isBookmarked ? `/api/posts/${encodeURIComponent(post_id)}/unbookmark` : `/api/posts/${encodeURIComponent(post_id)}/bookmark`;

  $.ajax({
    type: 'POST',
    url: endpoint,
    data: {
      post_id_give: post_id
    },
    success: function (res) {
      if (res.result === 'success' || (res.result === 'fail' && res.msg === 'Post already bookmarked')) {
        let nowBookmarked = Boolean(res.is_bookmarked);
        if (nowBookmarked) {
          $icon.removeClass('fa-bookmark-o').addClass('fa-bookmark text-primary');
          $btn.addClass('text-primary font-bold has-text-primary');
        } else {
          $icon.removeClass('fa-bookmark text-primary').addClass('fa-bookmark-o');
          $btn.removeClass('text-primary font-bold has-text-primary');

          if (window.location.pathname.startsWith('/bookmarks')) {
            $(`#${post_id}`).fadeOut(300, function () {
              $(this).remove();
              let remaining = $('#post-box > article, #post-box > .box').length;
              if (remaining === 0) {
                render_empty_bookmarks();
                $('#load-more-container').addClass('hidden');
              }
            });
            let $counter = $('#bookmark-count-display');
            if ($counter.length) {
              let count = parseInt($counter.text() || '0', 10);
              $counter.text(Math.max(0, count - 1));
            }
          }
        }
      } else {
        alert(res.msg || 'Gagal mengubah status bookmark.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal mengubah status bookmark.');
    }
  });
}

function open_edit_post(post_id) {
  $(`#post-text-${post_id}`).addClass('hidden').hide();
  $(`#edit-box-${post_id}`).removeClass('hidden is-hidden').show();
  $(`#edit-textarea-${post_id}`).focus();
}

function cancel_edit_post(post_id) {
  $(`#edit-box-${post_id}`).addClass('hidden is-hidden').hide();
  $(`#post-text-${post_id}`).removeClass('hidden').show();
}

function submit_edit_post(post_id) {
  let newContent = $(`#edit-textarea-${post_id}`).val().trim();
  if (!newContent) {
    alert('Isi postingan tidak boleh kosong.');
    return;
  }
  if (newContent.length > 5000) {
    alert('Isi postingan melebihi batas 5000 karakter.');
    return;
  }

  let $btn = $(`#edit-box-${post_id} button.btn-save-edit`);
  $btn.prop('disabled', true).text('Menyimpan...');

  $.ajax({
    type: 'POST',
    url: `/api/posts/${encodeURIComponent(post_id)}/edit`,
    data: {
      post_id_give: post_id,
      comment_give: newContent
    },
    success: function (res) {
      if (res.result === 'success') {
        $(`#post-text-${post_id}`).text(res.comment || newContent).removeClass('hidden').show();
        $(`#edit-box-${post_id}`).addClass('hidden is-hidden').hide();
        $(`#edited-badge-${post_id}`).removeClass('hidden is-hidden').show();
      } else {
        alert(res.msg || 'Gagal memperbarui postingan.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memperbarui postingan.');
    },
    complete: function () {
      $btn.prop('disabled', false).text('Simpan');
    }
  });
}

function delete_post(post_id) {
  if (!confirm('Apakah Anda yakin ingin menghapus postingan ini? Tindakan ini tidak dapat dibatalkan.')) {
    return;
  }

  $.ajax({
    type: 'POST',
    url: `/api/posts/${encodeURIComponent(post_id)}/delete`,
    data: {
      post_id_give: post_id
    },
    success: function (res) {
      if (res.result === 'success') {
        $(`#${post_id}`).fadeOut(300, function () {
          $(this).remove();
          let remaining = $('#post-box > article, #post-box > .box').length;
          if (remaining === 0) {
            if (window.location.pathname.startsWith('/bookmarks')) {
              render_empty_bookmarks();
            } else {
              get_posts();
            }
          }
        });
      } else {
        alert(res.msg || 'Gagal menghapus postingan.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal menghapus postingan.');
    }
  });
}

// ==========================================
// NOTIFICATION SYSTEM (SWEETER V2)
// ==========================================

function update_notification_badges(count) {
  let num = parseInt(count || '0', 10);
  let display = num > 99 ? '99+' : num.toString();

  let $navBadge = $('#nav-unread-badge');
  let $headerBadge = $('#header-unread-badge');
  let $pageBadge = $('#page-unread-count');

  if (num > 0) {
    if ($navBadge.length) {
      $navBadge.text(display).removeClass('hidden');
    }
    if ($headerBadge.length) {
      $headerBadge.text(display).removeClass('hidden');
    }
    if ($pageBadge.length) {
      $pageBadge.text(num).removeClass('hidden');
    }
  } else {
    if ($navBadge.length) {
      $navBadge.addClass('hidden');
    }
    if ($headerBadge.length) {
      $headerBadge.addClass('hidden');
    }
    if ($pageBadge.length) {
      $pageBadge.text('0');
    }
  }
}

function fetch_unread_notification_count() {
  $.ajax({
    type: 'GET',
    url: '/api/notifications/unread_count',
    success: function (res) {
      if (res.result === 'success') {
        update_notification_badges(res.unread_count);
      }
    }
  });
}

function render_empty_notifications() {
  $('#notifications-box').html(`
    <div class="text-center py-16 px-6 bg-surface-container-lowest rounded-xl shadow-sm my-2" id="notifications-empty-state">
      <span class="material-symbols-outlined text-[48px] text-outline-variant mb-2 block mx-auto">notifications_off</span>
      <h3 class="font-headline-sm text-[18px] font-bold text-on-surface mb-1">Belum ada notifikasi</h3>
      <p class="font-body-sm text-secondary max-w-sm mx-auto mb-4 leading-relaxed">
        Saat seseorang mengikuti Anda, bereaksi, atau mengomentari kiriman Anda, pemberitahuan akan muncul di sini.
      </p>
      <a href="/" class="inline-block px-5 py-2 rounded-full bg-primary-container text-on-primary font-label-md text-label-md hover:bg-primary transition-all border-0 cursor-pointer shadow-sm no-underline">
        Kembali ke Timeline
      </a>
    </div>
  `);
}

function render_notification_item(notif) {
  let actor = notif.actor_info || {};
  let actorName = escapeHtml(actor.profile_name || notif.actor);
  let actorUsername = escapeHtml(notif.actor);
  let avatarUrl = sanitizeAvatarUrl(actor.profile_pic_real || 'profile_pics/profile_placeholder.png');
  let timeStr = time2str(new Date(notif.created_at));
  let isRead = Boolean(notif.is_read);

  // Icon & styling per tipe notifikasi
  let typeIcon = 'notifications';
  let typeBg = 'bg-primary-container text-on-primary';
  let targetUrl = '/';

  if (notif.type === 'follow') {
    typeIcon = 'person_add';
    typeBg = 'bg-primary-container text-on-primary';
    targetUrl = `/user/${encodeURIComponent(notif.actor)}`;
  } else if (notif.type === 'reaction') {
    if (notif.action_type === 'stars') {
      typeIcon = 'star';
      typeBg = 'bg-[#e69138] text-white';
    } else if (notif.action_type === 'thumbs') {
      typeIcon = 'thumb_up';
      typeBg = 'bg-[#3d85c6] text-white';
    } else {
      typeIcon = 'favorite';
      typeBg = 'bg-error text-white';
    }
    targetUrl = notif.post_exists && notif.post_id ? `/#${encodeURIComponent(notif.post_id)}` : 'javascript:void(0)';
  } else if (notif.type === 'comment') {
    typeIcon = 'chat_bubble';
    typeBg = 'bg-secondary text-white';
    targetUrl = notif.post_exists && notif.post_id ? `/#${encodeURIComponent(notif.post_id)}` : 'javascript:void(0)';
  } else if (notif.type === 'reply') {
    typeIcon = 'reply';
    typeBg = 'bg-tertiary-container text-on-tertiary-container';
    targetUrl = notif.post_exists && notif.post_id ? `/#${encodeURIComponent(notif.post_id)}` : 'javascript:void(0)';
  } else if (notif.type === 'repost') {
    typeIcon = 'repeat';
    typeBg = 'bg-primary-container text-on-primary';
    targetUrl = notif.post_exists && notif.post_id ? `/#${encodeURIComponent(notif.post_id)}` : 'javascript:void(0)';
  } else if (notif.type === 'quote') {
    typeIcon = 'format_quote';
    typeBg = 'bg-primary-container text-on-primary';
    targetUrl = notif.post_exists && notif.post_id ? `/#${encodeURIComponent(notif.post_id)}` : 'javascript:void(0)';
  }

  let postSnippetHtml = '';
  if (notif.post_snippet) {
    postSnippetHtml = `
      <div class="mt-1.5 p-2 rounded-lg bg-surface-container-low text-on-surface-variant font-caption text-caption line-clamp-2 italic border-l-2 border-outline-variant">
        "${escapeHtml(notif.post_snippet)}"
      </div>
    `;
  } else if (notif.post_id && !notif.post_exists) {
    postSnippetHtml = `
      <div class="mt-1 text-on-surface-variant font-caption text-caption italic opacity-75">
        (Postingan ini telah dihapus)
      </div>
    `;
  }

  let cardBg = isRead
    ? 'bg-surface-container-lowest hover:bg-surface-container-low'
    : 'bg-surface-container-high/60 border-l-4 border-primary hover:bg-surface-container-high';

  let unreadIndicator = isRead
    ? ''
    : `<span class="w-2.5 h-2.5 rounded-full bg-primary shrink-0" id="unread-dot-${notif._id}"></span>`;

  let markReadBtn = isRead
    ? ''
    : `
      <button type="button"
              onclick="mark_single_notification_read('${notif._id}', event)"
              class="p-1.5 rounded-full hover:bg-surface-container text-on-surface-variant hover:text-primary transition-colors border-0 bg-transparent cursor-pointer shrink-0"
              title="Tandai telah dibaca"
              id="mark-read-btn-${notif._id}">
        <span class="material-symbols-outlined text-[20px]">check_circle</span>
      </button>
    `;

  return `
    <article class="p-space-md rounded-xl transition-all shadow-sm cursor-pointer mb-space-sm flex items-start gap-space-md ${cardBg}"
             id="notif-card-${notif._id}"
             onclick="handle_notification_click('${notif._id}', '${targetUrl}', ${isRead})">
      <!-- Actor Avatar with mini type badge -->
      <div class="relative shrink-0">
        <a href="/user/${encodeURIComponent(notif.actor)}" onclick="event.stopPropagation();">
          <img class="w-11 h-11 rounded-full object-cover shrink-0" src="${avatarUrl}" alt="${actorName}" onerror="this.src='/static/profile_pics/profile_placeholder.png'"/>
        </a>
        <span class="absolute -bottom-1 -right-1 w-5 h-5 rounded-full flex items-center justify-center text-[12px] shadow-sm ${typeBg}">
          <span class="material-symbols-outlined text-[12px] leading-none">${typeIcon}</span>
        </span>
      </div>

      <!-- Notification Content -->
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-1.5 flex-wrap">
          <a href="/user/${encodeURIComponent(notif.actor)}" onclick="event.stopPropagation();" class="font-label-md text-label-md text-on-surface font-semibold hover:underline no-underline">
            ${actorName}
          </a>
          <span class="font-body-sm text-body-sm text-on-surface-variant">
            ${escapeHtml(notif.message)}
          </span>
        </div>

        ${postSnippetHtml}

        <div class="flex items-center gap-2 mt-1.5 text-on-surface-variant font-caption text-caption">
          <span>${timeStr}</span>
        </div>
      </div>

      <!-- Read state & Action -->
      <div class="flex items-center gap-space-xs shrink-0 self-center">
        ${unreadIndicator}
        ${markReadBtn}
      </div>
    </article>
  `;
}

function load_notifications(cursor, append = false) {
  if (window.notifsLoading) return;
  window.notifsLoading = true;

  if (append) {
    $('#notif-loading').removeClass('hidden');
    $('#notif-load-more-btn').prop('disabled', true).html('<i class="fa fa-spinner fa-spin mr-1"></i> Memuat...');
  } else {
    $('#notif-load-more-container').addClass('hidden');
    $('#notifications-box').html(`
      <div class="text-center text-on-surface-variant py-10 font-body-sm">
        <i class="fa fa-spinner fa-spin text-xl text-primary mr-2"></i> Memuat notifikasi…
      </div>
    `);
  }

  let requestUrl = '/api/notifications?limit=20';
  if (cursor) {
    requestUrl += `&cursor=${encodeURIComponent(cursor)}`;
  }

  $.ajax({
    type: 'GET',
    url: requestUrl,
    success: function (res) {
      if (res.result === 'success') {
        let notifs = res.notifications || [];
        window.notifsNextCursor = res.next_cursor;
        window.notifsHasMore = Boolean(res.has_more);

        update_notification_badges(res.unread_count);

        if (!append) {
          $('#notifications-box').empty();
        }

        if (!notifs || notifs.length === 0) {
          if (!append) {
            render_empty_notifications();
          }
          $('#notif-load-more-container').addClass('hidden');
          return;
        }

        for (let i = 0; i < notifs.length; i++) {
          $('#notifications-box').append(render_notification_item(notifs[i]));
        }

        if (window.notifsHasMore) {
          $('#notif-load-more-container').removeClass('hidden');
          $('#notif-load-more-btn').prop('disabled', false).html('Load More');
        } else {
          $('#notif-load-more-container').addClass('hidden');
        }
      } else {
        handle_ajax_error(null, res.msg || 'Gagal memuat notifikasi.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal memuat notifikasi.');
    },
    complete: function () {
      window.notifsLoading = false;
      $('#notif-loading').addClass('hidden');
    }
  });
}

function handle_notification_click(notificationId, targetUrl, isRead) {
  if (isRead) {
    if (targetUrl && targetUrl !== 'javascript:void(0)') {
      window.location.href = targetUrl;
    }
    return;
  }

  // Tandai sebagai dibaca sebelum navigasi
  $.ajax({
    type: 'POST',
    url: `/api/notifications/${encodeURIComponent(notificationId)}/read`,
    success: function (res) {
      if (res.result === 'success') {
        update_notification_badges(res.unread_count);
        let $card = $(`#notif-card-${notificationId}`);
        $card.removeClass('bg-surface-container-high/60 border-l-4 border-primary')
             .addClass('bg-surface-container-lowest');
        $(`#unread-dot-${notificationId}`).remove();
        $(`#mark-read-btn-${notificationId}`).remove();
      }
    },
    complete: function () {
      if (targetUrl && targetUrl !== 'javascript:void(0)') {
        window.location.href = targetUrl;
      }
    }
  });
}

function mark_single_notification_read(notificationId, event) {
  if (event) {
    event.stopPropagation();
  }

  let $btn = $(`#mark-read-btn-${notificationId}`);
  $btn.prop('disabled', true);

  $.ajax({
    type: 'POST',
    url: `/api/notifications/${encodeURIComponent(notificationId)}/read`,
    success: function (res) {
      if (res.result === 'success') {
        update_notification_badges(res.unread_count);
        let $card = $(`#notif-card-${notificationId}`);
        $card.removeClass('bg-surface-container-high/60 border-l-4 border-primary')
             .addClass('bg-surface-container-lowest');
        $(`#unread-dot-${notificationId}`).fadeOut(200, function() { $(this).remove(); });
        $btn.fadeOut(200, function() { $(this).remove(); });
      } else {
        alert(res.msg || 'Gagal menandai notifikasi dibaca.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal menandai notifikasi dibaca.');
    }
  });
}

function mark_all_notifications_read() {
  let $btn = $('#mark-all-read-btn');
  $btn.prop('disabled', true).addClass('opacity-75');

  $.ajax({
    type: 'POST',
    url: '/api/notifications/read_all',
    success: function (res) {
      if (res.result === 'success') {
        update_notification_badges(0);
        // Ubah tampilan semua kartu notifikasi menjadi sudah dibaca
        $('#notifications-box > article').each(function () {
          $(this).removeClass('bg-surface-container-high/60 border-l-4 border-primary')
                 .addClass('bg-surface-container-lowest');
        });
        $('[id^="unread-dot-"]').remove();
        $('[id^="mark-read-btn-"]').remove();
      } else {
        alert(res.msg || 'Gagal menandai semua notifikasi dibaca.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal menandai semua notifikasi dibaca.');
    },
    complete: function () {
      $btn.prop('disabled', false).removeClass('opacity-75');
    }
  });
}

// Global auto-fetch badge jika ada elemen badge pada halaman
$(document).ready(function () {
  if ($('#nav-unread-badge').length || $('#header-unread-badge').length) {
    fetch_unread_notification_count();
  }
});

// --- Repost & Quote Post Client Functions ---

function hide_all_repost_dropdowns() {
  $('[id^="repost-dropdown-"]').addClass('hidden is-hidden');
}

function toggle_repost_dropdown(postId, event) {
  if (event) {
    event.stopPropagation();
  }
  let $dd = $(`#repost-dropdown-${postId}`);
  let isCurrentlyHidden = $dd.hasClass('hidden') || $dd.hasClass('is-hidden');
  hide_all_repost_dropdowns();
  if (isCurrentlyHidden) {
    $dd.removeClass('hidden is-hidden');
  }
}

$(document).on('click', function (e) {
  if (!$(e.target).closest('.repost-menu-container').length) {
    hide_all_repost_dropdowns();
  }
});

function toggle_repost(postId) {
  let p = window.postsCache ? window.postsCache[postId] : null;
  let isCurrentlyReposted = p ? Boolean(p.repost_by_me) : false;
  let endpoint = isCurrentlyReposted
    ? `/api/posts/${encodeURIComponent(postId)}/unrepost`
    : `/api/posts/${encodeURIComponent(postId)}/repost`;

  $.ajax({
    type: 'POST',
    url: endpoint,
    success: function (res) {
      if (res.result === 'success') {
        let isReposted = Boolean(res.is_reposted);
        let newCount = res.count_repost !== undefined ? res.count_repost : 0;
        if (p) {
          p.repost_by_me = isReposted;
          p.count_repost = newCount;
        }

        // Update UI counters and icons across both Stitch and Bulma
        let $icon = $(`#repost-icon-${postId}`);
        let $btn = $icon.closest('.btn-repost');
        let $num = $(`#repost-num-${postId}`);

        let $iconBulma = $(`#repost-icon-bulma-${postId}`);
        let $btnBulma = $iconBulma.closest('.btn-repost');
        let $numBulma = $(`#repost-num-bulma-${postId}`);

        if (isReposted) {
          $icon.addClass('text-primary');
          $btn.addClass('text-primary font-bold');
          $iconBulma.addClass('has-text-primary');
          $btnBulma.addClass('has-text-primary has-text-weight-bold');
        } else {
          $icon.removeClass('text-primary');
          $btn.removeClass('text-primary font-bold');
          $iconBulma.removeClass('has-text-primary');
          $btnBulma.removeClass('has-text-primary has-text-weight-bold');
        }

        $num.text(num2str(newCount));
        $numBulma.text(num2str(newCount));

        // If on user profile feed, refresh feed so repost appear or disappear
        if (window.currentProfileUsername) {
          get_posts(window.currentProfileUsername, window.currentFeedTab || 'following', '', false);
        }
      } else {
        alert(res.msg || 'Operasi repost gagal.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Operasi repost gagal.');
    }
  });
}

function open_quote_modal(postId) {
  let p = window.postsCache ? window.postsCache[postId] : null;
  $('#quote-target-id').val(postId);
  $('#quote-textarea').val('');

  if (p) {
    $('#quote-preview-avatar').attr('src', sanitizeAvatarUrl(p.profile_pic_real));
    $('#quote-preview-name').text(p.profile_name || p.username);
    $('#quote-preview-username').text(`@${p.username}`);
    $('#quote-preview-comment').text(p.comment || '');
    $('#quote-preview-card').removeClass('hidden is-hidden');
  } else {
    $('#quote-preview-card').addClass('hidden is-hidden');
  }

  $('#quote-modal').removeClass('hidden is-hidden').addClass('is-active flex');
  setTimeout(function () {
    $('#quote-textarea').focus();
  }, 100);
}

function close_quote_modal() {
  $('#quote-modal').addClass('hidden is-hidden').removeClass('is-active flex');
  $('#quote-target-id').val('');
  $('#quote-textarea').val('');
}

function submit_quote_post() {
  let targetId = $('#quote-target-id').val();
  let comment = $('#quote-textarea').val().trim();

  if (!comment) {
    alert('Komentar kutipan tidak boleh kosong.');
    return;
  }

  if (comment.length > 5000) {
    alert('Komentar kutipan melebihi batas maksimal 5000 karakter.');
    return;
  }

  let $btn = $('#btn-submit-quote');
  $btn.prop('disabled', true).addClass('opacity-75');

  $.ajax({
    type: 'POST',
    url: `/api/posts/${encodeURIComponent(targetId)}/quote`,
    contentType: 'application/json',
    data: JSON.stringify({ comment: comment }),
    success: function (res) {
      if (res.result === 'success') {
        close_quote_modal();
        // Refresh feed
        if (typeof get_posts === 'function') {
          get_posts(window.currentProfileUsername || '', window.currentFeedTab || 'following', '', false);
        } else if (typeof load_bookmarks === 'function' && window.isBookmarksPage) {
          load_bookmarks('', false);
        }
      } else {
        alert(res.msg || 'Gagal mengirim kutipan.');
      }
    },
    error: function (xhr) {
      handle_ajax_error(xhr, 'Gagal mengirim kutipan.');
    },
    complete: function () {
      $btn.prop('disabled', false).removeClass('opacity-75');
    }
  });
}

$(document).on('keydown', function (e) {
  if (e.key === 'Escape' && !$('#quote-modal').hasClass('hidden')) {
    close_quote_modal();
  }
});
