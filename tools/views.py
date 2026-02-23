from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.db.models import Q
from datetime import date
from .models import Category, Tool


def home(request):
    """首页视图"""
    search_query = request.GET.get('q', '').strip()
    search_results = None

    if search_query:
        search_results = Tool.objects.filter(
            Q(name__icontains=search_query) |
            Q(short_description__icontains=search_query) |
            Q(full_description__icontains=search_query),
            is_published=True
        )

    featured_tools = Tool.objects.filter(is_published=True, is_featured=True)[:6]
    recent_tools = Tool.objects.filter(is_published=True)[:6]
    hot_tools = Tool.objects.filter(is_published=True).order_by('-view_count')[:6]

    # 每日推荐：基于日期选择工具
    tools = Tool.objects.filter(is_published=True)
    if tools.exists():
        day_index = date.today().toordinal() % tools.count()
        daily_tool = tools[day_index]
    else:
        daily_tool = None

    categories = Category.objects.all()
    tool_count = Tool.objects.filter(is_published=True).count()
    category_count = categories.count()

    context = {
        'featured_tools': featured_tools,
        'recent_tools': recent_tools,
        'hot_tools': hot_tools,
        'daily_tool': daily_tool,
        'categories': categories,
        'tool_count': tool_count,
        'category_count': category_count,
        'search_query': search_query,
        'search_results': search_results,
        'today': date.today(),
    }
    return render(request, 'tools/home.html', context)


def tool_list(request):
    """工具列表视图"""
    tools = Tool.objects.filter(is_published=True)
    categories = Category.objects.all()
    selected_category = request.GET.get('category')

    if selected_category:
        tools = tools.filter(category__slug=selected_category)

    context = {
        'tools': tools,
        'categories': categories,
        'selected_category': selected_category,
    }
    return render(request, 'tools/tool_list.html', context)


def tool_detail(request, slug):
    """工具详情视图"""
    tool = get_object_or_404(Tool, slug=slug, is_published=True)
    tool.view_count += 1
    tool.save(update_fields=['view_count'])

    related_tools = Tool.objects.filter(
        category=tool.category,
        is_published=True
    ).exclude(id=tool.id)[:3]

    context = {
        'tool': tool,
        'related_tools': related_tools,
    }
    return render(request, 'tools/tool_detail.html', context)


def robots_txt(request):
    """robots.txt视图"""
    lines = [
        "User-agent: *",
        "Allow: /",
        "Sitemap: {}/sitemap.xml".format(request.build_absolute_uri('/').rstrip('/')),
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
