"""Native ttk settings editor for the Python tray. No browser or local web service."""
import json


def setting_text(value):
    if value is None:return 'none'
    if type(value) is bool:return 'true' if value else 'false'
    return str(value)


def filtered_settings(entries, group, query='', advanced=False):
    query=query.strip().casefold()
    return [e for e in entries if (query in ' '.join(str(e.get(k,'')) for k in ('key','label','note')).casefold()
        if query else e.get('group_id')==group and (advanced or not e.get('advanced')))]


def settings_changes(entries, values):
    return {e['key']:values[e['key']] for e in entries if e['key'] in values and values[e['key']]!=setting_text(e.get('pending') if e.get('has_pending') else e.get('value'))}


def show_settings(control, command_menus):
    import queue
    import threading
    import tkinter as tk
    from tkinter import ttk
    root=tk.Tk();root.title('WorldQuant');root.geometry('980x700');root.minsize(880,580)
    root.option_add('*Font',('Segoe UI',10))
    replies=queue.Queue();state={'snapshot':{},'group':'general','busy':False};values={};variables={}
    zh=True
    def t(a,b):return a if zh else b
    groups=[('general','通用','General'),('research','研究与额度','Research & limits'),('scheduling','运行与泳道','Scheduling & lanes'),('models','模型与路由','Models & routing'),('simulation','模拟与提交','Simulation & submission'),('providers','调用与花费','Calls & spending'),('authorization','授权期限','Authorization')]
    side=ttk.Frame(root,padding=16);side.pack(side='left',fill='y')
    ttk.Label(side,text='WorldQuant',font=('Segoe UI',16,'bold')).pack(anchor='w',pady=(0,20))
    content=ttk.Frame(root,padding=24);content.pack(side='right',fill='both',expand=True)
    query=tk.StringVar();ttk.Entry(content,textvariable=query).pack(fill='x')
    title=ttk.Label(content,text='',font=('Segoe UI',20,'bold'));title.pack(anchor='w',pady=(20,10))
    advanced=tk.BooleanVar();ttk.Checkbutton(content,text='显示高级参数 / Show advanced settings',variable=advanced,command=lambda:render()).pack(anchor='w')
    footer=ttk.Frame(content);footer.pack(side='bottom',fill='x',pady=(12,0))
    notice=tk.StringVar(value='Loading…');ttk.Label(footer,textvariable=notice,wraplength=630).pack(anchor='w')
    buttons=ttk.Frame(footer);buttons.pack(fill='x',pady=(10,0))
    canvas=tk.Canvas(content,highlightthickness=0);scroll=ttk.Scrollbar(content,orient='vertical',command=canvas.yview)
    scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
    body=ttk.Frame(canvas);item=canvas.create_window((0,0),window=body,anchor='nw')
    body.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
    def entries():return state['snapshot'].get('operating',{}).get('entries',[])
    def dirty():return settings_changes(entries(),values)
    def footer_state():
        count=len(dirty());save.configure(state='normal' if count and not state['busy'] else 'disabled')
        revert.configure(state='normal' if count and not state['busy'] else 'disabled')
        cancel.configure(state='normal' if state['snapshot'].get('operating',{}).get('pending') and not state['busy'] and not count else 'disabled')
        notice.set(t(f'{count} 项尚未保存',f'{count} unsaved changes') if count else t('修改已保存，等待现有任务结束。','Saved changes are waiting for active work.') if state['snapshot'].get('operating',{}).get('pending') else t('设置已同步','Settings are up to date'))
    def edit(key,var):values[key]=var.get();footer_state()
    def switch(group):state['group']=group;query.set('');render()
    for key,a,b in groups:
        ttk.Button(side,text=a+' / '+b,command=lambda key=key:switch(key)).pack(fill='x',pady=3)
    def async_call(action,arg=None):
        if state['busy']:return
        state['busy']=True;footer_state();notice.set(t('正在处理…','Working…'))
        sent=dict(dirty()) if action=='settings-save' else {}
        if action=='settings-save':arg=json.dumps({'values':sent,'revision':state['snapshot'].get('operating',{}).get('revision')})
        def worker():
            try:
                if action=='_provider-add':
                    from . import providers
                    from .desktop_control import _cfg
                    spec,key=arg;result=providers.install_custom(_cfg(),spec,key)
                    result['message']='Model saved; applies after existing work finishes.'
                else:result=control(action,arg)
                snapshot=control('settings') if action!='settings' else result
                replies.put((result,snapshot,sent,None))
            except Exception as exc:replies.put((None,None,sent,str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    def collect():
        nonlocal zh
        try:
            while True:
                result,snapshot,sent,error=replies.get_nowait();state['busy']=False
                if error:footer_state();notice.set(error);continue
                for key,value in sent.items():
                    if values.get(key)==value:values.pop(key,None)
                state['snapshot']=snapshot;zh=snapshot.get('language','zh')=='zh';render()
                if result.get('message'):notice.set(result['message'])
        except queue.Empty:pass
        root.after(100,collect)
    def invoke(action,arg):
        if dirty():notice.set(t('请先保存或撤销当前编辑。','Save or revert your edits first.'));return
        if action=='provider-add':
            add_provider();return
        async_call(action,arg)
    def add_provider():
        dialog=tk.Toplevel(root);dialog.title(t('添加模型','Add model'));dialog.transient(root)
        frame=ttk.Frame(dialog,padding=20);frame.pack(fill='both',expand=True)
        fields={}
        for index,(key,label,default) in enumerate([('name',t('名称','Name'),''),('protocol',t('协议','Protocol'),'openai'),('base_url',t('服务地址','Base URL'),'http://127.0.0.1:11434/v1'),('model',t('模型 ID','Model ID'),''),('key','API Key','')]):
            ttk.Label(frame,text=label).grid(row=index,column=0,sticky='w',pady=6)
            var=tk.StringVar(value=default);fields[key]=var
            widget=ttk.Combobox(frame,textvariable=var,values=['openai','anthropic'],state='readonly') if key=='protocol' else ttk.Entry(frame,textvariable=var,width=36,show='*' if key=='key' else '')
            widget.grid(row=index,column=1,padx=12,pady=6)
        no_key=tk.BooleanVar();ttk.Checkbutton(frame,text=t('服务不需要 API Key','Service requires no API key'),variable=no_key).grid(row=5,column=1,sticky='w')
        def submit():
            spec={key:var.get().strip() for key,var in fields.items() if key!='key'};spec['allow_no_key']=no_key.get()
            key=fields['key'].get();dialog.destroy();async_call('_provider-add',(spec,key))
        ttk.Button(frame,text=t('保存','Save'),command=submit).grid(row=6,column=1,sticky='e',pady=16)
    def build_menu(menu,items):
        for row in items:
            kind=row['kind']
            if kind=='sep':menu.add_separator()
            elif kind=='submenu':
                child=tk.Menu(menu,tearoff=False);build_menu(child,row['items']);menu.add_cascade(label=row['text'],menu=child)
            elif kind=='info':menu.add_command(label=row['text'],state='disabled')
            else:menu.add_command(label=('✓ ' if row.get('checked') else '')+row['text'],command=lambda row=row:invoke(row['action'],row.get('arg')))
    def render():
        variables.clear()
        for child in body.winfo_children():child.destroy()
        title.configure(text=t('搜索结果','Search results') if query.get() else next((t(a,b) for g,a,b in groups if g==state['group']),''))
        rows=filtered_settings(entries(),state['group'],query.get(),advanced.get())
        for entry in rows:
            key=entry['key'];value=values.get(key,setting_text(entry.get('pending') if entry.get('has_pending') else entry.get('value')))
            row=ttk.Frame(body,padding=(0,12));row.pack(fill='x')
            labels=ttk.Frame(row);labels.pack(side='left',fill='x',expand=True)
            ttk.Label(labels,text=entry['label'],font=('Segoe UI',10,'bold'),wraplength=340).pack(anchor='w')
            notes=[entry.get('note',''),entry.get('application','')]
            if entry.get('used') is not None:notes.insert(0,t('已用 ','Used ')+str(entry['used']))
            if entry.get('has_pending'):notes.insert(0,t('当前 ','Current ')+setting_text(entry.get('value'))+t('；待应用 ','; pending ')+setting_text(entry.get('pending')))
            if entry.get('active') is False:notes.insert(0,t('当前模式不使用此项','Inactive in the current mode'))
            ttk.Label(labels,text=' · '.join(n for n in notes if n),wraplength=340,foreground='#666666').pack(anchor='w',pady=(4,0))
            var=tk.StringVar(value=value);variables[key]=var
            if entry['type']=='boolean':widget=ttk.Checkbutton(row,text=t('开启','Enabled'),variable=var,onvalue='true',offvalue='false')
            elif entry['type']=='choice':
                options=entry['choices'];choice=tk.StringVar(value=next((v['label'] for v in options if v['value']==value),value))
                widget=ttk.Combobox(row,textvariable=choice,values=[v['label'] for v in options],state='readonly',width=19)
                widget.bind('<<ComboboxSelected>>',lambda _,options=options,choice=choice,var=var:var.set(next(v['value'] for v in options if v['label']==choice.get())))
            else:
                widget=ttk.Entry(row,textvariable=var,width=24 if entry['type']=='deadline' else 16)
                if entry.get('nullable'):ttk.Label(labels,text=t('none：','none: ')+entry.get('empty_label',''),foreground='#666666').pack(anchor='w')
                if entry['type']=='deadline':ttk.Label(labels,text=t('日期须带时区，如 +08:00','Include a timezone, e.g. +08:00'),foreground='#666666').pack(anchor='w')
            widget.pack(side='right',anchor='n',padx=(12,0));var.trace_add('write',lambda *_,key=key,var=var:edit(key,var))
            ttk.Separator(body).pack(fill='x')
        if state['group'] in ('models','scheduling') and not query.get():
            for label,items in command_menus(state['snapshot'],'zh' if zh else 'en',state['group']):
                button=ttk.Menubutton(body,text=label);menu=tk.Menu(button,tearoff=False);build_menu(menu,items);button.configure(menu=menu);button.pack(anchor='w',pady=12)
        if not rows and state['group']!='models':ttk.Label(body,text=t('没有匹配的设置。','No matching settings.')).pack(anchor='w',pady=20)
        footer_state()
    def revert_all():values.clear();render()
    save=ttk.Button(buttons,text='保存更改 / Save changes',command=lambda:async_call('settings-save',json.dumps(dirty())));save.pack(side='right')
    revert=ttk.Button(buttons,text='撤销编辑 / Revert edits',command=revert_all);revert.pack(side='right',padx=8)
    cancel=ttk.Button(buttons,text='取消待应用修改 / Cancel pending',command=lambda:async_call('operating-cancel'));cancel.pack(side='left')
    query.trace_add('write',lambda *_:render())
    root.bind('<Control-s>',lambda _:async_call('settings-save',json.dumps(dirty())) if dirty() else None)
    collect();async_call('settings');root.mainloop()
