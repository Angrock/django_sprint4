from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.timezone import now
from django.views.generic import (
    ListView,
    DetailView,
    UpdateView,
    CreateView,
    DeleteView,
)

from core.utils import post_all_query, post_published_query, get_post_data, get_paginated_page
from core.mixins import CommentMixinView
from .models import Post, User, Category, Comment
from .forms import UserEditForm, PostEditForm, CommentEditForm


class MainPostListView(ListView):
    """Главная страница со списком постов."""
    model = Post
    template_name = "blog/index.html"
    
    def get_queryset(self):
        return post_published_query().with_comment_count()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_obj'] = get_paginated_page(self.get_queryset(), self.request, per_page=10)
        return context


class CategoryPostListView(MainPostListView):
    """Страница со списком постов выбранной категории."""
    template_name = "blog/category.html"
    category = None

    def get_queryset(self):
        slug = self.kwargs["category_slug"]
        self.category = get_object_or_404(Category, slug=slug, is_published=True)
        return super().get_queryset().filter(category=self.category).with_comment_count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["category"] = self.category
        context['page_obj'] = get_paginated_page(self.get_queryset(), self.request, per_page=10)
        return context


class UserPostsListView(MainPostListView):
    """Страница со списком постов пользователя."""
    template_name = "blog/profile.html"
    author = None

    def get_queryset(self):
        username = self.kwargs["username"]
        self.author = get_object_or_404(User, username=username)
        if self.author == self.request.user:
            queryset = post_all_query().filter(author=self.author)
        else:
            queryset = super().get_queryset().filter(author=self.author)
        return queryset.with_comment_count().select_related('category')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["profile"] = self.author
        context['page_obj'] = get_paginated_page(self.get_queryset(), self.request, per_page=10)
        if self.author == self.request.user:
            context["total_posts"] = post_all_query().filter(author=self.author).count()
        else:
            context["total_posts"] = post_published_query().filter(author=self.author).count()
        return context


class PostDetailView(DetailView):
    """Страница выбранного поста."""
    model = Post
    template_name = "blog/detail.html"
    post_data = None
    pk_url_kwarg = "post_id"  # Указываем, что первичный ключ берется из параметра post_id

    def get_queryset(self):
        self.post_data = get_object_or_404(Post, pk=self.kwargs["post_id"])
        if self.post_data.author == self.request.user:
            return post_all_query().filter(pk=self.kwargs["post_id"]).with_comment_count()
        return post_published_query().filter(pk=self.kwargs["post_id"]).with_comment_count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.check_post_data():
            context["flag"] = True
            context["form"] = CommentEditForm()
        context["comments"] = self.object.comments.all().select_related("author")
        return context

    def check_post_data(self):
        """Вернуть результат проверки поста."""
        return all(
            (
                self.post_data.is_published,
                self.post_data.pub_date <= now(),
                self.post_data.category.is_published,
            )
        )


class UserProfileUpdateView(LoginRequiredMixin, UpdateView):
    """Обновление профиля пользователя."""
    model = User
    form_class = UserEditForm
    template_name = "blog/user.html"

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        username = self.request.user
        return reverse("blog:profile", kwargs={"username": username})


class PostCreateView(LoginRequiredMixin, CreateView):
    """Создание поста."""
    model = Post
    form_class = PostEditForm
    template_name = "blog/create.html"

    def form_valid(self, form):
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        username = self.request.user
        return reverse("blog:profile", kwargs={"username": username})


class PostUpdateView(LoginRequiredMixin, UpdateView):
    """Редактирование поста."""
    model = Post
    form_class = PostEditForm
    template_name = "blog/create.html"
    pk_url_kwarg = "post_id"

    def dispatch(self, request, *args, **kwargs):
        if self.get_object().author != request.user:
            return redirect("blog:post_detail", post_id=self.kwargs["post_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        post_id = self.kwargs["post_id"]
        return reverse("blog:post_detail", kwargs={"post_id": post_id})


class PostDeleteView(LoginRequiredMixin, DeleteView):
    """Удаление поста."""
    model = Post
    template_name = "blog/create.html"
    pk_url_kwarg = "post_id"

    def dispatch(self, request, *args, **kwargs):
        if self.get_object().author != request.user:
            return redirect("blog:post_detail", post_id=self.kwargs["post_id"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = PostEditForm(instance=self.object)
        return context

    def get_success_url(self):
        username = self.request.user
        return reverse_lazy("blog:profile", kwargs={"username": username})


class CommentCreateView(LoginRequiredMixin, CreateView):
    """Создание комментария."""
    model = Comment
    form_class = CommentEditForm
    template_name = "blog/comment.html"
    post_data = None

    def dispatch(self, request, *args, **kwargs):
        self.post_data = get_post_data(self.kwargs)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.author = self.request.user
        form.instance.post = self.post_data
        if self.post_data.author != self.request.user:
            self.send_author_email()
        return super().form_valid(form)

    def get_success_url(self):
        post_id = self.kwargs["post_id"]
        return reverse("blog:post_detail", kwargs={"post_id": post_id})

    def send_author_email(self):
        post_url = self.request.build_absolute_uri(self.get_success_url())
        recipient_email = self.post_data.author.email
        subject = "New comment"
        message = (
            f"Пользователь {self.request.user} добавил "
            f"комментарий к посту {self.post_data.title}.\n"
            f"Читать комментарий {post_url}"
        )
        send_mail(
            subject=subject,
            message=message,
            from_email="from@example.com",
            recipient_list=[recipient_email],
            fail_silently=True,
        )


class CommentUpdateView(CommentMixinView, UpdateView):
    """Редактирование комментария."""
    form_class = CommentEditForm
    pk_url_kwarg = "comment_id"
    def get_success_url(self):
        post_id = self.kwargs["post_id"]
        return reverse("blog:post_detail", kwargs={"post_id": post_id})


class CommentDeleteView(CommentMixinView, DeleteView):
    pk_url_kwarg = "comment_id"

    """Удаление комментария."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.pop('form', None)
        return context

    def get_success_url(self):
        post_id = self.kwargs["post_id"]
        return reverse("blog:post_detail", kwargs={"post_id": post_id})