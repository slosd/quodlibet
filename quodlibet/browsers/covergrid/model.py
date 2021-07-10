from gi.repository import GObject, Gio


class AlbumListItem(GObject.Object):
    def __init__(self, item):
        super().__init__()
        self.__item = item

    def scan_cover(self, force=False, scale_factor=1, cancel=None):
        def callback():
            self.notify('cover')
        return self.__item.scan_cover(force, scale_factor, callback, cancel)

    @GObject.Property
    def album(self):
        return self.__item.album

    @GObject.Property
    def cover(self):
        return self.__item.cover


class AlbumListModel(Gio.ListStore):
    def __init__(self, model):
        super().__init__()
        self.model = model

        model.connect('row-changed', self.__row_changed)
        model.connect('row-inserted', self.__row_inserted)
        model.connect('row-deleted', self.__row_deleted)
        model.connect('rows-reordered', self.__rows_reordered)

        self.splice(0, 0, [AlbumListItem(item) for item in model.itervalues()])

    def __row_changed(self, model, path, iter):
        position = path.get_indices()[0]
        self.get_item(position).notify('album')

    def __row_inserted(self, model, path, iter):
        position = path.get_indices()[0]
        self.insert(position, AlbumListItem(model.get_value(iter)))

    def __row_deleted(self, model, path):
        position = path.get_indices()[0]
        self.remove(position)

    def __rows_reordered(self, model, path, iter, new_order):
        self.splice(0, self.get_n_items(),
            [AlbumListItem(item) for item in model.itervalues()])