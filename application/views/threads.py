import re
import urllib
from datetime import datetime
from os.path import join

from flask import Blueprint, request, render_template, url_for, flash, redirect
from flask_user import login_required
from flask_user import login_required, current_user
from flask_babel import gettext as _
from flask_mail import Message
from sqlalchemy import or_, desc

from application import db, app, limiter, mail
from application.users import User
from application.threads import *
from application.utils import menu_bar, get_labels
from application.models import Project

threads = Blueprint('threads', __name__, url_prefix='/threads')

@limiter.exempt
@threads.route('/<project>/tagsthreads/<filetag>')
def tagthreads(project, filetag):
    # Find threads with a certain filetag
    menu = menu_bar(project)
    project = Project.query.filter_by(name=project).first_or_404()
    threads = (Thread.query.join(File_Tag)
               .filter(File_Tag.filename.like('%' + filetag + '%'))
               .filter(Thread.project==project)
               .order_by(desc(Thread.posted_at)))
    description = (_('Threads with tag: ') + '&nbsp <span class="uk-label">'
                   + filetag + '</span>')
    return render_template('search_comments.html', menu=menu, threads=threads,
                           description=description)

@threads.route('/<project>/search_comments', methods = ['GET', 'POST'])
def search_comments(project):
    menu = menu_bar(project)
    project = Project.query.filter_by(name=project).first_or_404()
    form = CommentSearchForm(request.form)
    if request.method == 'POST' and form.validate():
        if not form.search.data:
            form.search.data = ""
        threads = (
            Thread.query.filter(Thread.project==project)
            .join(Comment)
            .filter(Comment.thread_id==Thread.id)
            .filter(or_(Comment.content.like('%' + form.search.data + '%'),
                        Thread.title.like('%' + form.search.data + '%')))
            .order_by(desc(Thread.posted_at))
            .limit(100))
    else:
        threads = (Thread.query
                   .filter_by(project=project)
                   .order_by(desc(Thread.posted_at))
                   .limit(100))
    return render_template('search_comments.html', menu=menu, threads=threads,
                           form=form, show_discussion=True)

@threads.route('/<project>/newthread', methods = ['GET', 'POST'])
@login_required
def newthread(project):
    menu = menu_bar(project)
    project = Project.query.filter_by(name=project).first_or_404()
    inputs = {'user_tags': '', 'file_tags': '',
              'custom_tags': '', 'free_tags': ''}
    for t in inputs:
        inputs[t] = request.args.get(t) if request.args.get(t) else ''
    form = NewThreadForm(request.form,
                         user_tags=inputs['user_tags'].split(","),
                         file_tags=inputs['file_tags'].split(","),
                         custom_tags=inputs['custom_tags'].split(","),
                         free_tags=inputs['free_tags'])
    form.user_tags.widget.choices = json.dumps(
        [u.username for u in User.query.all()])
    form.file_tags.widget.choices = get_labels(project)
    form.custom_tags.widget.choices = json.dumps(
        [t.name for t in Named_Tag.query.filter_by(project=project).all()])
    if request.method == 'POST' and form.validate():
        # create thread
        new_thread = Thread(form.title.data,
                            current_user.id,
                            project.id,
                            form.flag.data,
                            datetime.utcnow())
        # add tags
        new_thread.user_tags = [User.get_by_name(n)
                                for n in form.user_tags.data]
        new_thread.file_tags = [File_Tag(new_thread.id, n)
                                for n in form.file_tags.data]
        new_thread.custom_tags = [Named_Tag.get_by_name(n)
                                  for n in form.custom_tags.data]
        new_thread.free_tags = [Free_Tag(new_thread.id, n)
                            for n in form.free_tags.data]
        # add first comment
        new_comment = Comment('000000:',
                              new_thread.id,
                              current_user.id,
                              form.firstcomment.data,
                              datetime.utcnow())
        new_thread.comments = [new_comment]
        db.session.add(new_thread)
        db.session.commit()
        # send emails
        if not app.config['TESTING']:
            with mail.connect() as conn:
                message_head = (
                    + _('Thread: ') + request.form['title'] + '\n'
                    + _('Project: ') + project + '\n'
                    + _('Owner: ') + current_user.username + '\n'
                    + _('Type: ') + request.form['flag'] + '\n'
                    + _('Created at: ') + str(datetime.utcnow()) + '\n'
                    + _('Contents:') + '\n\n')
                links = (_('To comment on this thread: ')
                         + url_for('threads.newcomment',
                                   project=project,
                                   thread_id = new_thread.id,
                                   _external = True))
                for user in form.user_tags.data:
                    user_obj = User.get_by_name(user)
                    message = (message_head + request.form['firstcomment']
                               + '\n\n' + links)
                    subject = _('Thread: ') + request.form['title']
                    msg = Message(recipients=[user_obj.email],
                                  body=message,
                                  subject=subject)
                    conn.send(msg)
        flash(_('New thread successfully created'), 'info')
        if 'return_url' in request.args:
            redirect(urllib.unquote(request.args['return_url']))
        else:
            return redirect(url_for('bookcloud.project', project=project.name))
    return render_template('newthread.html', menu=menu,
                           form=form, show_discussion=False)

@threads.route('/<project>/editthread/<thread_id>', methods = ['GET', 'POST'])
@login_required
def editthread(project, thread_id):
    menu = menu_bar(project)
    project = Project.query.filter_by(name=project).first_or_404()
    thread = Thread.get_by_id(thread_id)
    if (current_user != thread.owner and
        # Correct line below (remove username)!!!!!!!!!!!!!!!!!!!!!!!
        current_user.username != get_branch_owner(project, 'master')):
        # Not allowed
        flash(_('You are not allowed to edit this thread'), 'error')
        return redirect(url_for('bookcloud.project', project=project))
    form = ThreadForm(
        request.form,
        title=thread.title,
        flag=thread.flag,
        user_tags=[t.username for t in thread.user_tags],
        file_tags=[t.filename for t in thread.file_tags],
        custom_tags=[t.name for t in thread.custom_tags],
        free_tags=[t.name for t in thread.free_tags])
    form.user_tags.widget.choices = json.dumps(
        [u.username for u in User.query.all()])
    form.file_tags.widget.choices = get_labels(project)
    form.custom_tags.widget.choices = json.dumps(
        [t.name for t in Named_Tag.query.filter_by(project=project).all()])
    master_path = join('repos', project.name, 'master', 'source')
    if request.method == 'POST' and form.validate():
        thread.title = form.title.data
        thread.flag = form.flag.data
        # add tags
        thread.user_tags = [User.get_by_name(n)
                            for n in form.user_tags.data]
        thread.file_tags = [File_Tag(thread.id, n)
                            for n in form.file_tags.data]
        thread.custom_tags = [Named_Tag.get_by_name(n)
                              for n in form.custom_tags.data]
        thread.free_tags = [Free_Tag(thread.id, n)
                            for n in form.free_tags.data]
        db.session.commit()
        flash(_('Thread successfully modified'), 'info')
        if 'return_url' in request.args:
            return redirect(urllib.unquote(request.args['return_url']))
        else:
            return redirect(url_for('bookcloud.project', project=project.name))
    return render_template('editthread.html', menu=menu, form=form)

@threads.route('/<project>/newcomment/<thread_id>', methods = ['GET', 'POST'])
@threads.route('/<project>/newcomment/<thread_id>/<parent_lineage>',
               methods = ['GET', 'POST'])
@login_required
def newcomment(project, thread_id, parent_lineage=''):
    menu = menu_bar(project)
    form = CommentForm(request.form)
    if request.method == 'POST' and form.validate():
        project = Project.query.filter_by(name=project).first_or_404()
        siblings_pattern = parent_lineage + '%'
        decend_comments = (Comment.query
                           .filter(Comment.thread_id==thread_id)
                           .filter(Comment.lineage.like(siblings_pattern)))
        number_siblings = decend_comments.count()
        new_comment_lineage = (parent_lineage
                               + format(number_siblings, '06X') + ':')
        new_comment = Comment(new_comment_lineage,
                              thread_id,
                              current_user.id,
                              form.comment.data,
                              datetime.utcnow())
        db.session.add(new_comment)
        db.session.commit()
        # send emails
        if not app.config['TESTING']:
            with mail.connect() as conn:
                thread = Thread.get_by_id(thread_id)
                list_of_users = [ tag.username for tag in thread.user_tags]
                message_head = (
                    _('Thread: ') + thread.title + '\n' +\
                    _('Project: ') + project.id + '\n' +\
                    _('Author: ') + current_user.username + '\n' +\
                    _('Type: ') + thread.flag + '\n' +\
                    _('Created at: ') + str(datetime.utcnow()) + '\n' +\
                    _('Contents:') + '\n\n')
                links = (_('To reply to this comment follow: ')
                         + url_for('threads.newcomment',
                                   project=project.name,
                                   thread_id=thread_id,
                                   parent_lineage=new_comment_lineage,
                                   _external=True))
                for user in list_of_users:
                    user_obj = User.query.filter_by(username=user).first()
                    message = (message_head + request.form['comment']
                               + '\n\n' + links)
                    subject = _('Thread: ') + thread.title
                    msg = Message(recipients=[user_obj.email],
                                  body=message,
                                  subject=subject)
                    conn.send(msg)
        flash(_('New comment successfully created'), 'info')
        if 'return_url' in request.args:
            return redirect(urllib.unquote(request.args['return_url']))
    threads = (Thread.query.filter_by(id=thread_id)
               .order_by(desc(Thread.posted_at)))
    return render_template('newcomment.html', menu=menu,
                           form=form, threads=threads)

@threads.route('/<project>/editcomment/<comment_id>', methods = ['GET', 'POST'])
@login_required
def editcomment(project, comment_id):
    menu = menu_bar(project)
    comment = Comment.get_by_id(comment_id)
    print(comment.content)
    form = CommentForm(request.form,
                       comment=comment.content)
    if current_user != comment.owner:
        flash(_('You are not allowed to edit this comment'), 'error')
        if 'return_url' in request.args:
            return redirect(urllib.unquote(request.args['return_url']))
        else:
            return redirect(url_for('bookcloud.project', project=project))
    if request.method == 'POST' and form.validate():
            comment.content = form.comment.data
            db.session.commit()
            flash(_('Comment modified successfully'), 'info')
            if 'return_url' in request.args:
                return redirect(urllib.unquote(request.args['return_url']))
            else:
                return redirect(url_for('bookcloud.project', project=project))
    threads = (Thread.query.filter_by(id=comment.thread.id)
               .order_by(desc(Thread.posted_at)))
    return render_template('newcomment.html', menu=menu, form=form,
                           threads=threads)

@threads.route('/<project>/deletethread/<int:thread_id>')
@login_required
def deletethread(project, thread_id):
    thread = Thread.get_by_id(thread_id)
    if Comment.query.filter_by(thread_id=thread_id).first():
        flash(_('Thread is not empty'), 'error')
    else:
        thread = Thread.query.filter_by(id=thread_id).first()
        if not current_user.is_authenticated:
            flash(_('You must be logged in to delete a thread'), 'error')
        else:
            if (current_user != thread.owner
                and current_user != get_branch_owner(project, 'master')):
                # Not allowed
                flash(_('You are not allowed to delete this thread'), 'error')
            else:
                thread.delete()
                db.session.commit()
                flash(_('Thread successfully deleted'), 'info')
    if 'return_url' in request.args:
        return redirect(urllib.unquote(request.args['return_url']))
    else:
        return redirect(url_for('bookcloud.project', project=project))


@threads.route('/<project>/deletecomment/<int:comment_id>')
@login_required
def deletecomment(project, comment_id):
    comment = Comment.get_by_id(comment_id)
    if comment.has_replies():
        flash(_('This comment has replies and cannot be deleted'), 'error')
    else:
        if not current_user.is_authenticated:
            flash(_('You must be logged in to delete a comment'), 'error')
        else:
            if (current_user != comment.owner
                and current_user != get_branch_owner(project, 'master')):
                # Not allowed
                flash(_('You are not allowed '
                        'to delete this thread'), 'error')
            else:
                comment.delete()
                db.session.commit()
                flash(_('Comment successfully deleted'), 'info')
    if 'return_url' in request.args:
        return redirect(urllib.unquote(request.args['return_url']))
    else:
        return redirect(url_for('bookcloud.project', project=project))


