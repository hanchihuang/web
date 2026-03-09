from django.urls import path, re_path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('openclaw/', views.openclaw_column, name='openclaw_column'),
    path('tools/', views.tool_list, name='tool_list'),
    re_path(r'^tools/(?P<slug>[\w\-\u4e00-\u9fff]+)/$', views.tool_detail, name='tool_detail'),
]
